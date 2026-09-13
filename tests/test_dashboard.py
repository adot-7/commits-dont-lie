"""Offline rendering tests for dashboard routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from cdl.app import create_app
from cdl.config import Settings
from cdl.models import Claim, Entities, Evidence, Sentence, Verdict
from cdl.store import Store


def settings(tmp_path: Path) -> Settings:
    """Return dashboard settings with a test repository."""

    return Settings(
        "http://test", "admin", "github", "webhook", "adot-7/commits-dont-lie", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack-token", "signing", "C123", "anthropic", "model", str(tmp_path / "db.sqlite")
    )


def get(application, path: str) -> httpx.Response:
    """Run one ASGI request without the environment's sync TestClient issue."""

    async def run():
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(run())


def seed_post(store: Store) -> int:
    """Seed one Sent post and its evidence receipt."""

    post_id = store.create_post("note", "a" * 40, "b" * 40, "Sent", "I changed cdl/app.py.", notion_page_id="notion")
    sentence = Sentence(0, "I changed cdl/app.py.", "commits")
    claim = Claim(sentence, Entities(files=["cdl/app.py"]))
    store.add_sentences(post_id, [claim], [Verdict(0, "SUPPORTED", [Evidence("cdl/app.py", "file", "cdl/app.py")], reason="supported")])
    return post_id


def test_dashboard_and_detail_render_from_sqlite(tmp_path):
    """Home and detail pages expose counters and GitHub receipt links."""

    store = Store(tmp_path / "db.sqlite")
    post_id = seed_post(store)
    application = create_app(settings=settings(tmp_path), store=store)
    home = get(application, "/")
    detail = get(application, f"/posts/{post_id}")
    assert home.status_code == 200
    assert "Build updates with receipts" in home.text
    assert "Sent" in home.text
    assert '<meta http-equiv="refresh" content="20">' in home.text
    assert detail.status_code == 200
    assert "SUPPORTED" in detail.text
    assert 'http-equiv="refresh"' not in detail.text
    assert f"https://github.com/adot-7/commits-dont-lie/blob/{'b' * 40}/cdl/app.py" in detail.text


def test_detail_missing_returns_404_and_eval_without_report_is_honest(tmp_path):
    """Unknown posts 404 and an absent report gets a useful empty state."""

    store = Store(tmp_path / "db.sqlite")
    application = create_app(settings=settings(tmp_path), store=store)
    missing = get(application, "/posts/999")
    assert missing.status_code == 404
    eval_page = get(application, "/eval")
    assert eval_page.status_code == 200
    assert "No report yet" in eval_page.text or "Evaluation report" in eval_page.text


def test_dashboard_labels_unverifiable_sentence(tmp_path):
    """Detail pages show the no-checkable-claim receipt label."""

    store = Store(tmp_path / "db.sqlite")
    post_id = store.create_post("note", "a" * 40, "b" * 40, "Draft", "I kept going.")
    sentence = Sentence(0, "I kept going.", "notes")
    store.add_sentences(
        post_id,
        [Claim(sentence, Entities())],
        [Verdict(0, "UNVERIFIABLE", reason="Names no file, function, or integration that can be checked against the diff.")],
    )
    detail = get(create_app(settings=settings(tmp_path), store=store), f"/posts/{post_id}")
    assert "❔ no checkable claim" in detail.text


def test_eval_renders_staleness_metrics(tmp_path, monkeypatch):
    """The evaluation page displays the second, staleness report section."""

    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "report.json").write_text(
        json.dumps(
            {
                "accuracy": 1.0,
                "total": 1,
                "correct": 1,
                "labels": ["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"],
                "confusion_matrix": {label: {got: 0 for got in ["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]} for label in ["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]},
                "per_class": {},
                "staleness": {
                    "accuracy": 0.5,
                    "total": 2,
                    "correct": 1,
                    "cases": [],
                    "misses": [],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    page = get(create_app(settings=settings(tmp_path), store=Store(tmp_path / "db.sqlite")), "/eval")
    assert page.status_code == 200
    assert "Staleness cases" in page.text
    assert "0.5" in page.text
