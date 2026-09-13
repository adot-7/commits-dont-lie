"""FastAPI entry point for CDL HTTP routes.

The application owns request parsing, authentication, and background dispatch.
It must not contain grounding, drafting, or integration business logic; those
responsibilities stay in the service modules called by background tasks.
"""

from fastapi import FastAPI
from fastapi import BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
import hashlib
import hmac
import json
import subprocess
from typing import Any
from urllib.parse import parse_qs

try:
    from slack_sdk.signature import SignatureVerifier
except ImportError:  # pragma: no cover - exercised only in minimal local envs
    SignatureVerifier = None

from .config import Settings, get_settings
from .pipeline import handle_push
from .store import Store


class _FallbackSlackVerifier:
    """Small compatibility verifier used when slack_sdk is not installed."""

    def __init__(self, signing_secret: str):
        self.signing_secret = signing_secret.encode()

    def is_valid_request(self, body: bytes, headers: Any) -> bool:
        timestamp = headers.get("X-Slack-Request-Timestamp", "")
        supplied = headers.get("X-Slack-Signature", "")
        if not timestamp or not supplied:
            return False
        base = f"v0:{timestamp}:".encode() + body
        expected = "v0=" + hmac.new(self.signing_secret, base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, supplied)


def _verify_github(secret: str, body: bytes, supplied: str | None) -> bool:
    """Verify GitHub's HMAC-SHA256 signature over the raw request body."""

    if not secret or not supplied:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _git_sha(settings: Settings) -> str:
    """Return the configured build SHA or the current repository's short SHA."""

    if settings.git_sha:
        return settings.git_sha
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep event data useful while limiting it to webhook/application fields."""

    return {
        "ref": payload.get("ref"),
        "before": payload.get("before"),
        "after": payload.get("after"),
        "repository": (payload.get("repository") or {}).get("full_name"),
        "action": ((payload.get("actions") or [{}])[0] or {}).get("action_id"),
    }


async def _draft_now_background(
    head: str | None = None,
    *,
    store: Store,
    settings: Settings,
) -> None:
    """Resolve HEAD and run the drafter outside the HTTP acknowledgement."""

    from .drafter import maybe_draft
    from .github_client import GitHubClient

    github = GitHubClient(settings, store=store)
    try:
        target = head or github.head_sha("main")
        maybe_draft(target, settings=settings, store=store, github=github)
    except Exception as exc:
        store.append_event("error", {"component": "draft-now", "reason": str(exc)})


def create_app(
    *,
    settings: Settings | None = None,
    store: Store | None = None,
    push_handler: Any | None = None,
    approval_handler: Any | None = None,
    draft_handler: Any | None = None,
) -> FastAPI:
    """Build a FastAPI app with injectable settings and SQLite store."""

    active_settings = settings or get_settings(strict=False)
    active_store = store or Store(active_settings.database_path)
    active_push_handler = push_handler or handle_push
    active_draft_handler = draft_handler or _draft_now_background
    application = FastAPI(title="Commits Don't Lie")
    application.state.settings = active_settings
    application.state.store = active_store

    @application.get("/healthz")
    async def healthz() -> dict[str, Any]:
        """Return a lightweight process and build health response."""

        return {"ok": True, "sha": _git_sha(active_settings)}

    @application.get("/", response_class=HTMLResponse)
    async def home() -> str:
        """Render the M0 placeholder page."""

        return """<!doctype html><html><head><title>Commits Don't Lie</title></head>
        <body><h1>Commits Don't Lie — booting</h1></body></html>"""

    @application.post("/webhook/github")
    async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
        """Verify, persist, acknowledge, and background a GitHub push webhook."""

        body = await request.body()
        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        if not _verify_github(
            active_settings.github_webhook_secret,
            body,
            request.headers.get("X-Hub-Signature-256"),
        ):
            active_store.append_event(
                "error",
                {"component": "github_webhook", "reason": "invalid signature", "delivery_id": delivery_id},
            )
            return JSONResponse({"detail": "invalid signature"}, status_code=401)

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            active_store.append_event("error", {"component": "github_webhook", "reason": str(exc)})
            return JSONResponse({"detail": "invalid JSON"}, status_code=400)

        if not delivery_id:
            active_store.append_event("error", {"component": "github_webhook", "reason": "missing delivery id"})
            return JSONResponse({"detail": "missing delivery id"}, status_code=400)

        event_name = request.headers.get("X-GitHub-Event", "push")
        if event_name != "push" or payload.get("ref") != "refs/heads/main":
            active_store.append_event(
                "push.ignored",
                {**_event_payload(payload), "event": event_name, "delivery_id": delivery_id},
            )
            return JSONResponse({"ignored": True}, status_code=200)

        push_id = active_store.insert_push(
            delivery_id,
            str(payload.get("before", "")),
            str(payload.get("after", "")),
            str(payload.get("ref", "")),
            payload,
        )
        if push_id is None:
            active_store.append_event("push.duplicate", {"delivery_id": delivery_id})
            return JSONResponse({"duplicate": True}, status_code=200)

        active_store.append_event(
            "push.received",
            {**_event_payload(payload), "delivery_id": delivery_id},
            push_id=push_id,
        )
        background_tasks.add_task(active_push_handler, push_id, store=active_store)
        return JSONResponse({"accepted": True, "push_id": push_id}, status_code=202)

    @application.post("/slack/interactions")
    async def slack_interactions(request: Request, background_tasks: BackgroundTasks) -> Response:
        """Verify and acknowledge Slack interactions before backgrounding work."""

        body = await request.body()
        verifier = (
            SignatureVerifier(active_settings.slack_signing_secret)
            if SignatureVerifier is not None
            else _FallbackSlackVerifier(active_settings.slack_signing_secret)
        )
        if not verifier.is_valid_request(body, request.headers):
            active_store.append_event("error", {"component": "slack_interactions", "reason": "invalid signature"})
            return JSONResponse({"detail": "invalid signature"}, status_code=401)

        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        raw_payload = fields.get("payload", [""])[0]
        try:
            payload = json.loads(raw_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            active_store.append_event("error", {"component": "slack_interactions", "reason": str(exc)})
            return JSONResponse({"detail": "invalid payload"}, status_code=400)

        active_store.append_event("approval.received", _event_payload(payload))
        if approval_handler is None:
            from .approval import handle_interaction

            active_approval_handler = handle_interaction
        else:
            active_approval_handler = approval_handler

        background_tasks.add_task(active_approval_handler, payload, store=active_store)
        return Response(status_code=200)

    @application.post("/draft-now")
    async def draft_now(request: Request, background_tasks: BackgroundTasks) -> Response:
        """Authorize a manual draft trigger and acknowledge it immediately."""

        authorization = request.headers.get("Authorization", "")
        expected = f"Bearer {active_settings.admin_token}"
        if not active_settings.admin_token or not hmac.compare_digest(authorization, expected):
            active_store.append_event("error", {"component": "draft-now", "reason": "invalid admin token"})
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        head = request.query_params.get("head")
        background_tasks.add_task(
            active_draft_handler,
            head,
            store=active_store,
            settings=active_settings,
        )
        active_store.append_event("draft.now", {"head": head or "main"})
        return JSONResponse({"accepted": True}, status_code=202)

    return application


app = create_app()
