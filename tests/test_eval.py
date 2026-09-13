"""Offline tests for evaluation reports and compare caching."""

from __future__ import annotations

from pathlib import Path

from cdl.config import Settings
from cdl.eval.run_eval import run_eval
from cdl.models import DiffContext, Entities, FileChange
from cdl.store import Store


def settings(tmp_path: Path) -> Settings:
    """Return isolated evaluator settings."""

    return Settings(
        "http://test", "admin", "github", "webhook", "repo", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack-token", "signing", "C123", "anthropic", "model", str(tmp_path / "db.sqlite")
    )


class FakeGitHub:
    """Count compare calls and return a deterministic fixture diff."""

    def __init__(self):
        self.calls = 0

    def compare(self, base, head):
        self.calls += 1
        return DiffContext("repo", base, head, files=[FileChange("cdl/app.py", "modified", added=[(1, "def handle_push():")])])


def test_eval_writes_matrix_metrics_and_reuses_cache(tmp_path):
    """Full extraction and matcher-only rows are reported and compare is cached."""

    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "\n".join([
            '{"id":"true","base":"a","head":"b","sentence":"Changed cdl/app.py","expected":"SUPPORTED","entities":{"files":["cdl/app.py"]}}',
            '{"id":"false","base":"a","head":"b","sentence":"Changed missing.py","expected":"UNSUPPORTED","entities":{"files":["missing.py"]}}',
            '{"id":"vague","base":"a","head":"b","sentence":"Made progress","expected":"UNVERIFIABLE","entities":{}}',
        ]) + "\n"
    )
    stale_cases = tmp_path / "stale_cases.jsonl"
    stale_cases.write_text(
        '{"id":"kept","base":"a","head":"b","evidence":[{"entity":"handle_push","kind":"symbol","path":"cdl/app.py"}],"expected_stale":false,"note":"The symbol remains in the compared diff."}\n'
    )
    github = FakeGitHub()

    def extractor(sentence, paths, **kwargs):
        if "cdl/app.py" in sentence.text:
            return Entities(files=["cdl/app.py"])
        if "missing.py" in sentence.text:
            return Entities(files=["missing.py"])
        return Entities()

    kwargs = {
        "cases_path": cases,
        "stale_cases_path": stale_cases,
        "cache_dir": tmp_path / "cache",
        "report_json": tmp_path / "report.json",
        "report_md": tmp_path / "report.md",
        "github": github,
        "extractor": extractor,
        "settings": settings(tmp_path),
        "store": Store(tmp_path / "db.sqlite"),
    }
    first = run_eval(**kwargs)
    second = run_eval(**kwargs)
    assert first["correct"] == 3
    assert first["confusion_matrix"]["SUPPORTED"]["SUPPORTED"] == 1
    assert first["matcher_only"]["correct"] == 3
    assert first["staleness"]["correct"] == 1
    assert first["staleness"]["accuracy"] == 1.0
    assert github.calls == 1
    assert second["accuracy"] == 1.0
    assert "Confusion matrix" in (tmp_path / "report.md").read_text()
    assert "Staleness cases" in (tmp_path / "report.md").read_text()


def test_staleness_eval_reports_shared_matcher_results(tmp_path):
    """Stale receipts and retained receipts are both measured in the report."""

    cases = tmp_path / "cases.jsonl"
    cases.write_text('{"id":"claim","base":"claim-base","head":"claim-head","sentence":"Made progress","expected":"UNVERIFIABLE"}\n')
    stale_cases = tmp_path / "stale_cases.jsonl"
    stale_cases.write_text(
        "\n".join(
            [
                '{"id":"removed","base":"old","head":"new","evidence":[{"entity":"old_name","kind":"symbol","path":"cdl/app.py"}],"expected_stale":true,"note":"The old symbol was removed."}',
                '{"id":"kept","base":"same","head":"same-edit","evidence":[{"entity":"keep_name","kind":"symbol","path":"cdl/app.py"}],"expected_stale":false,"note":"The body edit kept the symbol."}',
            ]
        )
        + "\n"
    )

    class StalenessGitHub:
        def compare(self, base, head):
            if base == "old":
                return DiffContext("repo", base, head, files=[FileChange("cdl/app.py", "modified", removed=[(1, "def old_name():")])])
            if base == "same":
                return DiffContext(
                    "repo",
                    base,
                    head,
                    files=[FileChange("cdl/app.py", "modified", added=[(1, "def keep_name():")], removed=[(1, "def keep_name():")])],
                )
            return DiffContext("repo", base, head)

    report = run_eval(
        cases_path=cases,
        stale_cases_path=stale_cases,
        cache_dir=tmp_path / "cache",
        report_json=tmp_path / "report.json",
        report_md=tmp_path / "report.md",
        github=StalenessGitHub(),
        extractor=lambda *_args, **_kwargs: Entities(),
        settings=settings(tmp_path),
        store=Store(tmp_path / "db.sqlite"),
    )
    assert report["staleness"]["correct"] == 2
    assert report["staleness"]["accuracy"] == 1.0
    assert report["staleness"]["cases"][0]["got_stale"] is True
