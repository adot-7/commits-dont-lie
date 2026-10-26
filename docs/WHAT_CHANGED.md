# What changed for BharatCode Build League

Commits Don't Lie was first built solo in one night at the Multi-App AI Agent Hackathon
(Lemma × Comma Capital): first commit 13 Sep 2026 23:30 IST, last commit 14 Sep 2026 04:43 IST,
42 commits. That version is preserved unchanged on the branch
[`lemma-hackathon`](https://github.com/adot-7/commits-dont-lie/tree/lemma-hackathon).
Build League allows existing projects (organiser answer on Discord, 22 Oct 2026). Everything
below was added from 26 Oct 2026.

## Then and now

| | Sep 2026 (`lemma-hackathon`) | Oct–Nov 2026 (`main`) |
|---|---|---|
| Model calls | Anthropic Claude via the `anthropic` SDK | BharatCode `qwen-3.8-27b` via raw HTTPS to the OpenAI-compatible `/chat/completions`; Anthropic still selectable with `LLM_PROVIDER=anthropic` |
| How you try it | Set up GitHub webhook, Notion databases, Slack app, VM | `pip install` + one BharatCode key, then `cdl check` in any git repo |
| Diff source | GitHub compare API only | GitHub compare API (server) or the local repository (CLI, eval) |
| Eval | Harness existed; no published run | Re-run on BharatCode against local diffs; report committed |
| Tests | 65 at submission (2 of them broke once `slack_sdk` was installed, see below) | See `make test` |

## Built in this window

- **BharatCode provider** (`cdl/bharatcode_client.py`). One forced function tool per call.
  A `length` finish is never used as a partial tool call: retry once with double the output
  budget, then fail with the `x-request-id`. 429 and 503 are retried. Every call writes an
  `llm.call` event with tokens, ms and request ID.
- **Provider-neutral tool calls** in `cdl/llm.py`, so drafting, extraction and the substring
  guard are the same code for both providers.
- **Local diffs** (`cdl/local_git.py`). Same `DiffContext` as the GitHub path. Checked against
  the live GitHub compare API for `40cd3db...4fb4a7b`: 22 commits, 35 files, identical statuses
  and added/removed line numbers.
- **`cdl check` and `cdl draft`**. Zero-setup CLI using the same `claim_vs_diff` and the same
  publish gate as the server. Exit code 1 when a sentence would not ship, so it works as a
  pre-push hook or CI step.
- **`pyproject.toml`** with a `cdl` entry point.
- **Test fix.** Two Slack interaction tests signed requests with a fixed 2023 timestamp; the
  real `slack_sdk` verifier rejects timestamps older than five minutes, so they failed whenever
  the SDK was installed. They now sign with the current time.

## What did not change

The verdict rules, the alias table, the staleness monitor, the Notion/Slack/GitHub flows and the
dashboard are the September code. The rule that the model never decides a verdict is unchanged.
