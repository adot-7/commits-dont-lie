"""Command-line entry point for CDL operations.

This module owns argument parsing and delegates work to application modules.
It must stay thin: business rules belong in the corresponding service module,
not in command handlers.
"""

from __future__ import annotations

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    """Build the stable CLI command surface described by the architecture spec."""

    parser = argparse.ArgumentParser(prog="python -m cdl", description="Commits Don't Lie")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the FastAPI server")
    draft = subparsers.add_parser("draft-now", help="draft from the current or supplied HEAD")
    draft.add_argument("--head", help="head SHA to draft against")
    replay = subparsers.add_parser("replay", help="re-run a stored push")
    replay.add_argument("push_id", type=int)
    subparsers.add_parser("eval", help="run the hand-labelled evaluation")
    subparsers.add_parser("resolve-notion-ids", help="resolve Notion database data-source IDs")
    subparsers.add_parser("models", help="list available Anthropic model IDs")
    compare = subparsers.add_parser("dump-compare", help="dump a GitHub compare response as JSON")
    compare.add_argument("base")
    compare.add_argument("head")
    subparsers.add_parser("resync", help="retry failed external mirrors")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one command and return a process exit status."""

    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 0
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
    # Later milestones replace these placeholders with service dispatch.
    if args.command in {"draft-now", "replay", "eval", "resolve-notion-ids", "models", "dump-compare", "resync"}:
        print(f"{args.command}: not implemented yet")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
