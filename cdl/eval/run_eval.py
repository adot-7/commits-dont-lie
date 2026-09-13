"""Hand-labelled evaluation harness for the grounding pipeline.

This module owns cache files, full extraction-plus-matcher evaluation, the
optional matcher-only comparison, and report rendering. It must not be
imported by the FastAPI request path or alter production post state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import Settings, get_settings
from ..github_client import GitHubClient
from ..grounding.check import claim_vs_diff
from ..llm import extract_entities, filter_entities
from ..models import Claim, DiffContext, Entities, FileChange, Sentence, dataclass_dict
from ..store import Store


LABELS = ["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]


def load_cases(path: str | Path = "eval/cases.jsonl") -> list[dict[str, Any]]:
    """Load one JSON object per non-empty line from a hand-labelled case file."""

    case_path = Path(path)
    if not case_path.exists():
        raise FileNotFoundError(f"evaluation case file not found: {case_path}")
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(case_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on {case_path}:{line_number}: {exc}") from exc
        required = {"id", "base", "head", "sentence", "expected"}
        missing = required - value.keys()
        if missing:
            raise ValueError(f"case {line_number} missing: {', '.join(sorted(missing))}")
        if value["expected"] not in LABELS:
            raise ValueError(f"case {value['id']} has invalid expected verdict")
        cases.append(value)
    return cases


def _diff_from_dict(value: dict[str, Any]) -> DiffContext:
    """Restore a cached JSON DiffContext with tuple line pairs."""

    return DiffContext(
        repo=value["repo"],
        base_sha=value["base_sha"],
        head_sha=value["head_sha"],
        commits=[],
        files=[
            FileChange(
                path=item["path"],
                status=item["status"],
                previous_path=item.get("previous_path"),
                patch=item.get("patch"),
                added=[tuple(line) for line in item.get("added", [])],
                removed=[tuple(line) for line in item.get("removed", [])],
            )
            for item in value.get("files", [])
        ],
        truncated=bool(value.get("truncated", False)),
    )


def _cache_file(cache_dir: Path, base: str, head: str) -> Path:
    """Return a stable cache filename for one compare range."""

    return cache_dir / f"{base[:16]}_{head[:16]}.json"


def _get_diff(github: Any, cache_dir: Path, base: str, head: str) -> DiffContext:
    """Read a cached compare or fetch and cache it once."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_file(cache_dir, base, head)
    if path.exists():
        return _diff_from_dict(json.loads(path.read_text(encoding="utf-8")))
    diff = github.compare(base, head)
    path.write_text(json.dumps(dataclass_dict(diff), indent=2), encoding="utf-8")
    return diff


def _entities_from_case(value: dict[str, Any]) -> Entities:
    """Read optional hand-written matcher-only entities."""

    return Entities(
        files=list(value.get("files", [])),
        symbols=list(value.get("symbols", [])),
        integrations=list(value.get("integrations", [])),
    )


def _metrics(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a 3×3 matrix and one-vs-rest precision/recall values."""

    rows = list(cases)
    matrix = {expected: {got: 0 for got in LABELS} for expected in LABELS}
    for row in rows:
        matrix[row["expected"]][row["got"]] += 1
    per_class: dict[str, dict[str, float]] = {}
    for label in LABELS:
        true_positive = matrix[label][label]
        predicted = sum(matrix[expected][label] for expected in LABELS)
        actual = sum(matrix[label].values())
        per_class[label] = {
            "precision": round(true_positive / predicted, 3) if predicted else 0.0,
            "recall": round(true_positive / actual, 3) if actual else 0.0,
        }
    correct = sum(matrix[label][label] for label in LABELS)
    return {
        "labels": LABELS,
        "total": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 3) if rows else 0.0,
        "confusion_matrix": matrix,
        "per_class": per_class,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a compact report suitable for README/brief links."""

    lines = [
        "# Commits Don't Lie evaluation",
        "",
        f"Accuracy: **{report['correct']}/{report['total']} ({report['accuracy']:.1%})**",
        "",
        "## Confusion matrix",
        "",
        "| expected \\ got | " + " | ".join(report["labels"]) + " |",
        "|---|" + "---|" * len(report["labels"]),
    ]
    for label in report["labels"]:
        lines.append("| " + label + " | " + " | ".join(str(report["confusion_matrix"][label][got]) for got in report["labels"]) + " |")
    lines.extend(["", "## Per-class precision / recall", "", "| class | precision | recall |", "|---|---:|---:|"])
    for label in report["labels"]:
        metrics = report["per_class"][label]
        lines.append(f"| {label} | {metrics['precision']:.3f} | {metrics['recall']:.3f} |")
    if report.get("misses"):
        lines.extend(["", "## Misses", ""])
        lines.extend(
            f"- `{miss['id']}` expected `{miss['expected']}`, got `{miss['got']}` — {miss['reason']}"
            for miss in report["misses"]
        )
    if report.get("matcher_only"):
        lines.extend(["", "## Matcher-only cases", "", f"{report['matcher_only']['correct']}/{report['matcher_only']['total']} correct."])
    return "\n".join(lines) + "\n"


def run_eval(
    *,
    cases_path: str | Path = "eval/cases.jsonl",
    cache_dir: str | Path = "eval/cache",
    report_json: str | Path = "eval/report.json",
    report_md: str | Path = "eval/report.md",
    github: Any | None = None,
    extractor: Callable[..., Entities] | None = None,
    settings: Settings | None = None,
    store: Store | None = None,
) -> dict[str, Any]:
    """Run full extraction and optional matcher-only evaluation and write reports."""

    active_settings = settings or get_settings(strict=False)
    active_store = store or Store(active_settings.database_path)
    active_github = github or GitHubClient(active_settings, store=active_store)
    active_extractor = extractor or extract_entities
    cases = load_cases(cases_path)
    full_rows: list[dict[str, Any]] = []
    matcher_rows: list[dict[str, Any]] = []
    for case in cases:
        diff = _get_diff(active_github, Path(cache_dir), case["base"], case["head"])
        sentence = Sentence(0, case["sentence"], "both")
        extracted = active_extractor(sentence, [item.path for item in diff.files], settings=active_settings, store=active_store)
        filtered = filter_entities(
            sentence,
            extracted,
            event_sink=lambda kind, data: active_store.append_event(kind, data),
        )
        verdict = claim_vs_diff(Claim(sentence, filtered), diff)
        row = {"id": case["id"], "expected": case["expected"], "got": verdict.status, "reason": verdict.reason}
        full_rows.append(row)
        if "entities" in case:
            matcher_verdict = claim_vs_diff(Claim(sentence, _entities_from_case(case["entities"])), diff)
            matcher_rows.append({"id": case["id"], "expected": case["expected"], "got": matcher_verdict.status, "reason": matcher_verdict.reason})
    report = _metrics(full_rows)
    report["misses"] = [row for row in full_rows if row["expected"] != row["got"]]
    if matcher_rows:
        matcher_report = _metrics(matcher_rows)
        matcher_report.pop("confusion_matrix", None)
        matcher_report.pop("per_class", None)
        report["matcher_only"] = matcher_report
    report["cases"] = full_rows
    report_path = Path(report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path = Path(report_md)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    active_store.append_event("eval.completed", {"total": report["total"], "correct": report["correct"], "accuracy": report["accuracy"]})
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; fail when accuracy is below the documented threshold."""

    parser = argparse.ArgumentParser(description="Run the CDL hand-labelled evaluation")
    parser.add_argument("--cases", default="eval/cases.jsonl")
    parser.add_argument("--cache-dir", default="eval/cache")
    parser.add_argument("--report-json", default="eval/report.json")
    parser.add_argument("--report-md", default="eval/report.md")
    args = parser.parse_args(argv)
    try:
        report = run_eval(
            cases_path=args.cases,
            cache_dir=args.cache_dir,
            report_json=args.report_json,
            report_md=args.report_md,
            settings=get_settings(strict=True),
        )
    except Exception as exc:
        print(f"eval failed: {exc}")
        return 1
    print(f"{report['correct']}/{report['total']} ({report['accuracy']:.1%})")
    print(render_markdown(report))
    return 0 if report["accuracy"] >= 0.8 else 1
