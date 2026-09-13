# Commits Don't Lie

> Every build-in-public tool drafts a post that sounds like you. None of them check whether it is true. Ours does — and it keeps checking after you hit send.

Commits Don't Lie turns GitHub commits and Notion development notes into a build-update draft, then refuses to send any sentence it cannot point to in the diff. A human approves the grounded draft in Slack. Later pushes re-check every Sent post and thread a correction when cited code disappears.

Live dashboard: [commitsdontlie.akashparashar.dev](https://commitsdontlie.akashparashar.dev/) · [post audit view](https://commitsdontlie.akashparashar.dev/posts/1) · [reliability brief](BRIEF.md)

## The check

**Every file, function, or integration a sentence names must appear in the diff — or the sentence doesn't ship.**

| Verdict | Meaning |
| --- | --- |
| ✅ `SUPPORTED` | Every extracted entity matched; the post carries receipts. |
| ⛔ `UNSUPPORTED` | At least one named entity is missing; the post is blocked. |
| ❔ `UNVERIFIABLE` | The sentence names nothing checkable; it must be rewritten or dropped. |

The LLM drafts sentences and extracts entities. Python's deterministic `claim_vs_diff` function decides the verdict and records file/line evidence. The same matcher runs at the pre-publish gate and in the post-publish monitor.

## What is visible

The [dashboard](https://commitsdontlie.akashparashar.dev/) shows post status, commit ranges, sentence verdicts, evidence receipts, staleness, event-derived counters, LLM token totals, and estimated cost. Each post detail page links a receipt to the corresponding GitHub blob line. The [evaluation page](https://commitsdontlie.akashparashar.dev/eval) renders `eval/report.json` when the hand-labelled set has been run.

## Architecture

```text
GitHub push ──webhook──▶ /webhook/github ──202──▶ background pipeline
                                                     │
                                  ┌──────────────────┴──────────────────┐
                                  │ monitor Sent posts → Stale/correction│
                                  │ ready Notion note → compare → LLM    │
                                  │ extract → claim_vs_diff → gate       │
                                  └──────────────┬───────────────┬───────┘
                                                 │               │
                                           Notion mirror   Slack approval
Browser ──▶ SQLite-backed Jinja dashboard ◀────────────────────────────
```

One Python process, one uvicorn worker, FastAPI, synchronous `httpx` REST clients, Slack Web API, Anthropic tool use, Jinja, and SQLite. GitHub is ground truth; Notion is both the human note input and the post mirror; Slack is the approval and correction surface.

## Setup

1. Follow the [deployment and integration runbook](docs/07-DEPLOY.md): provision the small VM, create the GitHub webhook, Notion Notes/Posts data sources, and Slack app.
2. Copy `.env.example` to `.env`, fill the credentials, and never commit `.env`.
3. Install dependencies with `python3 -m pip install -r requirements.txt` (Python 3.12 is the target runtime). Initialize the database with the first server start.
4. Resolve and validate Notion IDs: `python -m cdl resolve-notion-ids`; verify the model with `python -m cdl models`.
5. Run locally with `make serve`; check `GET /healthz`. A push to `refs/heads/main` or an authenticated `POST /draft-now` starts the pipeline.

The CLI also exposes `dump-compare`, `replay <push_id>`, and `eval`. See the [architecture](docs/01-ARCHITECTURE.md), [data contracts](docs/02-DATA-CONTRACTS.md), and [integration calls](docs/03-INTEGRATIONS.md) for exact payloads.

## Evaluation

Create `eval/cases.jsonl` with 15 hand-labelled cases: five real commit ranges, each with a true named claim, a false named claim, and a vague claim. Then run:

```bash
make test
make eval
```

The harness caches GitHub compares in `eval/cache/`, runs extraction → substring filter → matcher, runs an optional matcher-only pass for hand-written entities, and writes `eval/report.json` plus `eval/report.md`. It exits non-zero below 80% accuracy.

## Limitations and future work

- The checker is lexical, not semantic: it verifies presence of a file, symbol, or integration, not whether the sentence characterizes the change correctly.
- Staleness is conservative for integrations and does not model semantic drift or world-state drift.
- This build monitors one public repository and one Slack channel; it has no auth/multi-user layer.
- It deliberately does not post to X/Twitter, use Notion Workers, support multi-repo workflows, or add a general hallucination detector.
- Future work: X posting after the paid write API is available, multi-repo/auth support, semantic claim checks, and richer correction editing.

## Project status

The source milestones are implemented as small issue-scoped commits on `main`. Production dogfooding, the hand-labelled eval set, recording, and submission remain operator steps because they require Akash's live Notion/Slack/GitHub accounts and approval.
