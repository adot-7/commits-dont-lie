"""Command-line entry point for CDL operations.

This module owns argument parsing and delegates work to application modules.
It must stay thin: business rules belong in the corresponding service module,
not in command handlers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    """Build the stable CLI command surface described by the architecture spec."""

    command = os.path.basename(sys.argv[0])
    prog = command if command in {"shakira", "cdl"} else "python -m cdl"
    parser = argparse.ArgumentParser(prog=prog, description="Shakira | Commits Don't Lie")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the FastAPI server")
    draft = subparsers.add_parser("draft-now", help="draft from the current or supplied HEAD")
    draft.add_argument("--head", help="head SHA to draft against")
    replay = subparsers.add_parser("replay", help="re-run a stored push")
    replay.add_argument("push_id", type=int)
    eval_parser = subparsers.add_parser("eval", help="run the hand-labelled evaluation")
    eval_parser.add_argument("--cases", default="eval/cases.jsonl")
    eval_parser.add_argument("--stale-cases", default="eval/stale_cases.jsonl")
    eval_parser.add_argument("--cache-dir", default="eval/cache")
    eval_parser.add_argument("--report-json", default="eval/report.json")
    eval_parser.add_argument("--report-md", default="eval/report.md")
    eval_parser.add_argument("--local-repo", help="read diffs from a local git repository instead of the GitHub API")
    subparsers.add_parser("resolve-notion-ids", help="resolve Notion database data-source IDs")
    subparsers.add_parser("models", help="list model IDs for the configured provider")
    compare = subparsers.add_parser("dump-compare", help="dump a GitHub compare response as JSON")
    compare.add_argument("base")
    compare.add_argument("head")
    subparsers.add_parser("resync", help="retry failed external mirrors")
    for name, help_text in (
        ("check", "check a post against a local commit range; exit 1 if any claim is unsupported"),
        ("draft", "draft a post from a local commit range and check every sentence"),
    ):
        local = subparsers.add_parser(name, help=help_text)
        if name == "check":
            local.add_argument("text", nargs="?", help="post text; use -f FILE or pipe it on stdin instead")
            local.add_argument("-f", "--file", help="read the post from a file ('-' for stdin)")
        else:
            local.add_argument("--note", default="", help="what you meant to say; the draft keeps your phrasing where the diff supports it")
        local.add_argument("-n", "--commits", type=int, default=None, help="check against the last N commits (default 1)")
        local.add_argument("-r", "--range", dest="range_spec", help="commit range such as main..HEAD or v1.0..v1.1")
        local.add_argument("--repo", default=".", help="path to the git repository (default: current directory)")
        local.add_argument("--json", action="store_true", help="print a machine-readable report")
        local.add_argument("--db", help="event log path (default: <repo>/.git/cdl/events.sqlite)")
        local.add_argument("-v", "--verbose", action="store_true", help="echo every logged event to stderr")
    return parser


def _local_command(args: argparse.Namespace) -> int:
    """Run ``check`` or ``draft`` against a local repository with only a model key."""

    from .config import PROVIDER_KEY_VARS, ConfigError, load_settings
    from .local_check import as_json, check_sentences, draft_and_check, render, split_sentences
    from .llm import LLMError
    from .local_git import LocalGit, LocalGitError
    from .store import Store

    try:
        settings = load_settings(strict=False)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    key_var = PROVIDER_KEY_VARS[settings.llm_provider]
    key = settings.bharatcode_api_key if settings.llm_provider == "bharatcode" else settings.anthropic_api_key
    if not key:
        print(f"error: {key_var} is not set. Get a key at https://bharatcode.ai/account and export it.", file=sys.stderr)
        return 2
    git = LocalGit(args.repo)
    try:
        if args.range_spec:
            base, head = git.parse_range(args.range_spec)
        else:
            base, head = git.last_n(args.commits or 1)
        diff = git.compare(base, head)
        db_path = args.db or str(git.git_dir() / "cdl" / "events.sqlite")
    except LocalGitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    store = Store(db_path, echo=sys.stderr if args.verbose else None)
    if args.command == "check":
        if args.file:
            try:
                text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
            except OSError as exc:
                print(f"error: cannot read {args.file}: {exc.strerror}", file=sys.stderr)
                return 2
        elif args.text:
            text = args.text
        elif not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print("error: pass the post text, -f FILE, or pipe it on stdin", file=sys.stderr)
            return 2
        sentences = split_sentences(text)
        if not sentences:
            print("error: the post is empty", file=sys.stderr)
            return 2
    try:
        if args.command == "check":
            result = check_sentences(sentences, diff, settings=settings, store=store)
        else:
            result = draft_and_check(args.note, diff, settings=settings, store=store)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(as_json(result, diff), indent=2, ensure_ascii=False))
    else:
        print(render(result, diff))
    return 0 if result.passed else 1


def main(argv: list[str] | None = None) -> int:
    """Dispatch one command and return a process exit status."""

    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 0
    if args.command in {"check", "draft"}:
        return _local_command(args)
    if args.command == "serve":
        import uvicorn

        from .config import load_settings
        from .notion_client import NotionClient

        settings = load_settings(strict=True)
        notion = NotionClient(settings=settings)
        try:
            notion.assert_schema()
        finally:
            notion.close()
        uvicorn.run("cdl.app:app", host="127.0.0.1", port=8000, workers=1)
        return 0
    if args.command == "dump-compare":
        from .config import load_settings
        from .github_client import GitHubClient
        from .models import dataclass_dict

        client = GitHubClient(settings=load_settings(strict=True))
        try:
            print(json.dumps(dataclass_dict(client.compare(args.base, args.head)), indent=2))
        finally:
            client.close()
        return 0
    if args.command == "resolve-notion-ids":
        from .config import load_settings
        from .notion_client import NotionClient

        client = NotionClient(settings=load_settings(strict=True))
        try:
            for name, value in client.resolve_data_source_ids().items():
                print(f"{name}={value}")
        finally:
            client.close()
        return 0
    if args.command == "models":
        from .config import load_settings
        from .llm import list_models

        for model_id in list_models(settings=load_settings(strict=True)):
            print(model_id)
        return 0
    if args.command == "draft-now":
        from .config import load_settings
        from .drafter import maybe_draft
        from .github_client import GitHubClient
        from .store import Store

        settings = load_settings(strict=True)
        store = Store(settings.database_path)
        github = GitHubClient(settings, store=store)
        try:
            head = args.head or github.head_sha("main")
            post_ids = maybe_draft(head, settings=settings, store=store, github=github)
            print(json.dumps({"head": head, "post_ids": post_ids}))
        finally:
            github.close()
        return 0
    if args.command == "replay":
        from .config import load_settings
        from .pipeline import handle_push
        from .store import Store

        settings = load_settings(strict=True)
        store = Store(settings.database_path)
        asyncio.run(handle_push(args.push_id, settings=settings, store=store))
        return 0
    if args.command == "eval":
        from .eval.run_eval import main as eval_main

        return eval_main([
            "--cases", args.cases,
            "--stale-cases", args.stale_cases,
            "--cache-dir", args.cache_dir,
            "--report-json", args.report_json,
            "--report-md", args.report_md,
            *(["--local-repo", args.local_repo] if args.local_repo else []),
        ])
    # Later milestones replace these placeholders with service dispatch.
    if args.command in {"draft-now", "replay", "eval", "resolve-notion-ids", "models", "dump-compare", "resync"}:
        print(f"{args.command}: not implemented yet")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
