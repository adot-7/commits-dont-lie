"""HTTP acceptance tests for the M0 walking skeleton."""

from __future__ import annotations

import hashlib
import asyncio
import hmac
import json

import httpx

from cdl.app import create_app
from cdl.config import Settings
from cdl.store import Store


def make_settings(tmp_path):
    """Build deterministic local settings for endpoint tests."""

    return Settings(
        app_base_url="http://testserver",
        admin_token="admin",
        github_token="token",
        github_webhook_secret="webhook-secret",
        github_repo="adot-7/commits-dont-lie",
        notion_token="notion",
        notion_notes_db_id="notes-db",
        notion_posts_db_id="posts-db",
        notion_notes_ds_id="notes-ds",
        notion_posts_ds_id="posts-ds",
        slack_bot_token="slack",
        slack_signing_secret="slack-signing-secret",
        slack_channel_id="C123",
        anthropic_api_key="anthropic",
        anthropic_model="claude-sonnet-5",
        database_path=str(tmp_path / "cdl.sqlite"),
    )


def signed_github(body: bytes, secret: str) -> str:
    """Return the GitHub signature format used by the route."""

    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signed_slack(body: bytes, secret: str, timestamp: str = "1700000000") -> str:
    """Return Slack's v0 signature format for the fallback verifier test."""

    base = f"v0:{timestamp}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


async def noop_push_handler(_push_id, *, store):
    """Keep endpoint tests focused on HTTP behavior, not external pipeline calls."""

    del store


async def noop_draft_handler(_head, *, store, settings):
    """Keep draft-now endpoint tests external-service free."""

    del store, settings


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    """Exercise the ASGI app without the environment's sync TestClient threadpool."""

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def test_webhook_rejects_bad_signature(tmp_path):
    """Unsigned requests are rejected before persistence."""

    store = Store(tmp_path / "cdl.sqlite")
    response = asyncio.run(
        request(
            create_app(settings=make_settings(tmp_path), store=store, push_handler=noop_push_handler),
            "POST",
            "/webhook/github",
            content=b"{}",
            headers={"X-GitHub-Delivery": "d1"},
        )
    )
    assert response.status_code == 401
    assert store.list_pushes() == []


def test_webhook_persists_and_deduplicates_push(tmp_path):
    """A signed main push is accepted once and duplicate delivery is a no-op."""

    settings = make_settings(tmp_path)
    store = Store(tmp_path / "cdl.sqlite")
    application = create_app(settings=settings, store=store, push_handler=noop_push_handler)
    payload = {
        "ref": "refs/heads/main",
        "before": "0" * 40,
        "after": "a" * 40,
        "repository": {"full_name": settings.github_repo},
    }
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Delivery": "d1",
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": signed_github(body, settings.github_webhook_secret),
    }
    first = asyncio.run(request(application, "POST", "/webhook/github", content=body, headers=headers))
    second = asyncio.run(request(application, "POST", "/webhook/github", content=body, headers=headers))
    assert first.status_code == 202
    assert second.status_code == 200
    assert len(store.list_pushes()) == 1


def test_health_and_placeholder(tmp_path):
    """The public M0 URLs exist without external credentials."""

    application = create_app(settings=make_settings(tmp_path), store=Store(tmp_path / "cdl.sqlite"), push_handler=noop_push_handler)
    health = asyncio.run(request(application, "GET", "/healthz"))
    home = asyncio.run(request(application, "GET", "/"))
    assert health.json()["ok"] is True
    assert "booting" in home.text


def test_slack_interaction_acknowledges_valid_signature(tmp_path):
    """Verified form payloads are logged and acknowledged immediately."""

    settings = make_settings(tmp_path)
    store = Store(tmp_path / "cdl.sqlite")
    application = create_app(settings=settings, store=store, push_handler=noop_push_handler)
    payload = json.dumps({"type": "block_actions", "actions": []})
    body = f"payload={payload}".encode()
    timestamp = "1700000000"
    response = asyncio.run(
        request(
            application,
            "POST",
            "/slack/interactions",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signed_slack(body, settings.slack_signing_secret, timestamp),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    )
    assert response.status_code == 200
    assert response.content == b""


def test_draft_now_requires_admin_token_and_acknowledges_valid_request(tmp_path):
    """Manual drafting is protected and returns 202 after scheduling work."""

    settings = make_settings(tmp_path)
    store = Store(tmp_path / "cdl.sqlite")
    application = create_app(
        settings=settings,
        store=store,
        draft_handler=noop_draft_handler,
    )
    unauthorized = asyncio.run(request(application, "POST", "/draft-now"))
    authorized = asyncio.run(
        request(application, "POST", "/draft-now?head=" + "b" * 40, headers={"Authorization": "Bearer admin"})
    )
    assert unauthorized.status_code == 401
    assert authorized.status_code == 202
    assert authorized.json()["accepted"] is True
