"""M0 smoke tests for imports and SQLite schema creation."""

from __future__ import annotations

import importlib

from cdl.store import init_db


def test_imports_and_init_db(tmp_path):
    """Every current package module imports and creates the working schema."""

    modules = [
        "cdl",
        "cdl.app",
        "cdl.approval",
        "cdl.config",
        "cdl.drafter",
        "cdl.github_client",
        "cdl.llm",
        "cdl.models",
        "cdl.monitor",
        "cdl.notion_client",
        "cdl.pipeline",
        "cdl.slack_client",
        "cdl.store",
        "cdl.grounding.check",
        "cdl.eval.run_eval",
    ]
    for module in modules:
        assert importlib.import_module(module)

    connection = init_db(tmp_path / "cdl.sqlite")
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"pushes", "posts", "sentences", "evidence", "events", "notes_seen"} <= tables
    connection.close()
