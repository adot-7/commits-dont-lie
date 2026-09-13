"""Normative unit coverage for deterministic grounding and staleness."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from cdl.grounding.check import INTEGRATION_ALIASES, claim_vs_diff, check_staleness
from cdl.llm import filter_entities
from cdl.models import Claim, DiffContext, Entities, Evidence, FileChange, Sentence


def mk_diff(*files: FileChange, truncated: bool = False) -> DiffContext:
    """Build compact fixture diffs for matcher tests."""

    return DiffContext(
        repo="adot-7/commits-dont-lie",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=list(files),
        truncated=truncated,
    )


def file(path: str, *, status: str = "modified", added=(), removed=(), patch="patch") -> FileChange:
    """Build a FileChange without repeating contract defaults."""

    return FileChange(path, status, None, patch, list(added), list(removed))


def claim(text: str, entities: Entities) -> Claim:
    """Build a first-person test claim."""

    return Claim(Sentence(0, text, "both"), entities)


def test_file_matches_full_path():
    verdict = claim_vs_diff(claim("Changed cdl/app.py", Entities(files=["cdl/app.py"])), mk_diff(file("cdl/app.py")))
    assert verdict.status == "SUPPORTED"
    assert verdict.evidence[0].path == "cdl/app.py"
    assert verdict.evidence[0].line_no is None


def test_file_matches_unique_basename():
    verdict = claim_vs_diff(claim("Changed app.py", Entities(files=["app.py"])), mk_diff(file("cdl/app.py")))
    assert verdict.status == "SUPPORTED"


def test_ambiguous_basename_does_not_match():
    verdict = claim_vs_diff(
        claim("Changed app.py", Entities(files=["app.py"])),
        mk_diff(file("cdl/app.py"), file("tests/app.py")),
    )
    assert verdict.status == "UNSUPPORTED"


def test_removed_file_does_not_support_claim():
    verdict = claim_vs_diff(
        claim("Removed cdl/app.py", Entities(files=["cdl/app.py"])),
        mk_diff(file("cdl/app.py", status="removed")),
    )
    assert verdict.status == "UNSUPPORTED"


def test_symbol_matches_added_line_and_receipt():
    verdict = claim_vs_diff(
        claim("Added claim_vs_diff", Entities(symbols=["claim_vs_diff"])),
        mk_diff(file("cdl/grounding/check.py", added=[(41, "def claim_vs_diff(claim, diff):")])),
    )
    assert verdict.status == "SUPPORTED"
    assert verdict.evidence[0].line_no == 41
    assert verdict.evidence[0].line_text.startswith("def claim_vs_diff")


def test_symbol_only_in_removed_line_does_not_match():
    verdict = claim_vs_diff(
        claim("Removed old_fn", Entities(symbols=["old_fn"])),
        mk_diff(file("cdl/a.py", removed=[(4, "def old_fn():")])),
    )
    assert verdict.status == "UNSUPPORTED"


def test_integration_alias_matches_changed_path():
    verdict = claim_vs_diff(
        claim("Wired Slack", Entities(integrations=["slack"])),
        mk_diff(file("cdl/slack_client.py")),
    )
    assert verdict.status == "SUPPORTED"
    assert verdict.evidence[0].line_no is None


def test_integration_matches_added_line():
    verdict = claim_vs_diff(
        claim("Added Notion query", Entities(integrations=["notion"])),
        mk_diff(file("cdl/client.py", added=[(8, "NOTION_VERSION = api_version")])),
    )
    assert verdict.status == "SUPPORTED"
    assert verdict.evidence[0].line_no == 8


def test_alias_table_is_normative():
    assert INTEGRATION_ALIASES["github"] == ["github", "x-hub-signature", "x-github"]
    assert INTEGRATION_ALIASES["fastapi"] == ["fastapi", "uvicorn"]


def test_no_entities_is_unverifiable_with_exact_reason():
    verdict = claim_vs_diff(claim("Made things better", Entities()), mk_diff())
    assert verdict.status == "UNVERIFIABLE"
    assert verdict.reason == "Names no file, function, or integration that can be checked against the diff."


def test_one_missing_entity_is_unsupported_with_reason():
    verdict = claim_vs_diff(
        claim("Changed cdl/app.py and missing_fn", Entities(files=["cdl/app.py"], symbols=["missing_fn"])),
        mk_diff(file("cdl/app.py")),
    )
    assert verdict.status == "UNSUPPORTED"
    assert verdict.missing == ["missing_fn"]
    assert "'missing_fn' does not appear" in verdict.reason


def test_missing_symbol_on_truncated_diff_explains_unavailable_patch():
    verdict = claim_vs_diff(
        claim("Changed missing_fn", Entities(symbols=["missing_fn"])),
        mk_diff(file("large.bin", patch=None), truncated=True),
    )
    assert verdict.status == "UNSUPPORTED"
    assert verdict.reason.endswith("(some patches unavailable)")


def test_staleness_file_removed():
    evidence = [Evidence("cdl/app.py", "file", "cdl/app.py")]
    result = check_staleness(evidence, mk_diff(file("cdl/app.py", status="removed")))
    assert result.stale is True
    assert result.removed == evidence


def test_staleness_file_renamed():
    evidence = [Evidence("cdl/app.py", "file", "cdl/app.py")]
    renamed = FileChange("cdl/application.py", "renamed", "cdl/app.py", "patch", [], [])
    assert check_staleness(evidence, mk_diff(renamed)).stale is True


def test_staleness_symbol_net_removed():
    evidence = [Evidence("old_fn", "symbol", "cdl/a.py", 4, "def old_fn():")]
    result = check_staleness(evidence, mk_diff(file("cdl/a.py", removed=[(4, "def old_fn():")])))
    assert result.stale is True
    assert "old_fn was removed" in result.reason


def test_staleness_symbol_moved_is_not_stale():
    evidence = [Evidence("moved_fn", "symbol", "cdl/a.py", 4, "def moved_fn():")]
    result = check_staleness(
        evidence,
        mk_diff(
            file("cdl/a.py", removed=[(4, "def moved_fn():")]),
            file("cdl/b.py", added=[(1, "def moved_fn():")]),
        ),
    )
    assert result.stale is False


def test_staleness_body_edit_keeps_symbol():
    evidence = [Evidence("keep_fn", "symbol", "cdl/a.py", 4, "def keep_fn():")]
    result = check_staleness(evidence, mk_diff(file("cdl/a.py", removed=[(5, "    return old")])))
    assert result.stale is False


def test_staleness_integration_requires_all_receipt_files_removed():
    evidence = [
        Evidence("slack", "integration", "cdl/slack_client.py"),
        Evidence("slack", "integration", "cdl/app.py"),
    ]
    one_removed = check_staleness(evidence, mk_diff(file("cdl/slack_client.py", status="removed"), file("cdl/app.py")))
    both_removed = check_staleness(
        evidence,
        mk_diff(file("cdl/slack_client.py", status="removed"), file("cdl/app.py", status="removed")),
    )
    assert one_removed.stale is False
    assert both_removed.stale is True


def test_substring_filter_drops_hallucinated_entity_and_emits_event():
    sentence = Sentence(2, "Changed cdl/app.py using FastAPI", "both")
    events = []
    filtered = filter_entities(
        sentence,
        Entities(files=["cdl/app.py", "cdl/secret.py"], integrations=["fastapi", "notion"]),
        event_sink=lambda kind, data: events.append((kind, data)),
    )
    assert filtered.files == ["cdl/app.py"]
    assert filtered.integrations == ["fastapi"]
    assert [(kind, data["entity"]) for kind, data in events] == [
        ("entity_dropped", "cdl/secret.py"),
        ("entity_dropped", "notion"),
    ]


def test_real_fixture_supports_a_real_sentence():
    payload = json.loads(Path("tests/fixtures/real_compare_1.json").read_text())
    diff = DiffContext(
        repo=payload["repo"],
        base_sha=payload["base_sha"],
        head_sha=payload["head_sha"],
        files=[
            FileChange(
                item["path"], item["status"], item["previous_path"], item["patch"],
                [tuple(line) for line in item["added"]], [tuple(line) for line in item["removed"]]
            )
            for item in payload["files"]
        ],
        truncated=payload["truncated"],
    )
    verdict = claim_vs_diff(
        claim("Added _verify_github in cdl/app.py", Entities(files=["cdl/app.py"], symbols=["_verify_github"])),
        diff,
    )
    assert verdict.status == "SUPPORTED"


def test_grounding_imports_only_stdlib_and_models():
    tree = ast.parse(Path("cdl/grounding/check.py").read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert imports == ["__future__", "models"]
