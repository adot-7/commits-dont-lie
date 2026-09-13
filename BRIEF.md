# What it does

Every file, function, or integration a sentence names must appear in the diff — or the sentence doesn't ship. `SUPPORTED` carries receipts, `UNSUPPORTED` blocks the post, and `UNVERIFIABLE` names nothing checkable. The deterministic `claim_vs_diff` function runs at the pre-publish gate and again in the post-publish monitor.

# How we know it works

`make test` passes 50 offline tests covering diff parsing, all verdict and staleness rules, tool-use validation, pipeline gating, approval mid-checks, corrections, dashboard rendering, and evaluation reporting. The production dashboard is the source of truth for pushes, post statuses, sentence verdict totals, dropped entities, LLM tokens, and estimated cost. This repository snapshot contains no production SQLite file, so those runtime counters are intentionally not invented here. The hand-labelled 15-case `make eval` result is also an operator-run artifact; once present, `eval/report.md` and `/eval` show its 3×3 matrix, per-class precision/recall, and misses.

# Where the LLM is and isn't

Anthropic drafts 3–6 first-person sentences and extracts files, symbols, and integrations through forced `tool_use` JSON schemas. It never decides a verdict. The substring guard removes any extracted entity not literally present in the sentence (files may also match by basename) and logs each drop. `ANTHROPIC_MODEL` controls the model, and each call records model, input/output tokens, duration, and failures in SQLite/stdout. The matcher is pure Python and has zero network or LLM imports.
The anthropic 1.5.0 SDK removed the `temperature` parameter from `messages.create`; the client feature-detects it and otherwise runs at the SDK default.

# Failure handling

| Failure | Shipped behavior |
| --- | --- |
| Bad webhook/Slack signature | Reject with 401 and log an error event. |
| GitHub compare failure | Retry transient failures, then log `compare.failed` and keep the affected flow visible as Errored. |
| Missing GitHub patch | Keep the file receipt but mark unavailable-symbol claims unsupported with an explanatory suffix. |
| Malformed LLM tool input | Retry once with the parse error; then raise `LLMError` and persist an Errored row. |
| Notion or Slack write failure | Keep SQLite state and mark the affected post Errored; external resync is an explicit follow-up operation. |
| Duplicate delivery/action | Use GitHub delivery ID or Slack action timestamp as an idempotency key. |
| Code changes before approval | Re-compare against HEAD; changed evidence becomes Stale and is never approved. |

# What it doesn't do

The check is lexical, not semantic. It tracks drift from this repository only, not regulations or third-party world state. The shipped scope is one repository, one channel, and one operator. There is no X posting, multi-repo/auth layer, general hallucination detector, or Notion Worker; the human copies an approved Slack draft to the final public channel.

# Architecture

One synchronous Python process runs FastAPI behind Caddy. A GitHub push is verified and acknowledged, then a background task compares the range, monitors Sent evidence, reads Ready Notion notes, calls Anthropic for draft/extraction only, applies the deterministic gate, mirrors the result to Notion, and waits for Slack approval. SQLite is the audit source for the Jinja dashboard.

```text
GitHub push ──webhook──▶ /webhook/github ──202──▶ background pipeline
                                                     │
                                  ┌──────────────────┴──────────────────┐
                                  │ monitor → Stale/correction           │
                                  │ note → compare → draft → extract    │
                                  │ → claim_vs_diff → Notion → Slack     │
                                  └──────────────────────────────────────┘
```
