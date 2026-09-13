# What it does

Every file, function, or integration a sentence names must appear in the diff — or the sentence doesn't ship. `SUPPORTED` carries receipts, `UNSUPPORTED` blocks the post, and `UNVERIFIABLE` names nothing checkable. The deterministic `claim_vs_diff` function runs at the pre-publish gate and again in the post-publish monitor.
Sentences that make no checkable claim are allowed (max one per post) and labelled as such.

# How we know it works

**Offline: `make test` — 65 tests pass** at submission. They cover the diff parser (hunk-header line numbers), every verdict rule (file / symbol / integration matching, alias expansion for compound names, evidence ranking and the 3-per-entity cap, the one-UNVERIFIABLE gate boundary), every staleness rule (removed file, renamed file, net-removed symbol; a *moved* symbol and a body edit are not stale), the substring guard, `tool_use` schema validation and the length-truncation fallback, the publish gate, the approval mid-check, corrections, dashboard rendering, and the eval harness itself.

**Hand-labelled evaluation set: `eval/cases.jsonl` — 30 cases.** Written in the author's voice against 8 real commits from this build: per commit one true claim, one plausible false claim naming code the commit did not touch, and one pure-voice sentence; plus 6 adversarial cases (real file in the wrong range, near-miss filename, multi-word integration, two entities with one missing, decoy integration, pure voice). Labels were verified against each commit's file list. `make eval` runs the full production path (extract → guard → match), writes a 3×3 confusion matrix with per-class precision/recall to `eval/report.md`, and `/eval` renders it. A second file, `eval/stale_cases.jsonl`, targets the staleness call site. **The report is operator-run; if `eval/report.md` is absent in this snapshot, the run had not completed at submission — the numbers are not invented here.**

**Live: the event log.** Every external call and every verdict is an append-only event in SQLite and a JSON line on stdout — `push.received`, `compare.fetched`, `llm.call` (model, tokens, ms), `verdict` (status, evidence, missing, reason), `entity_dropped`, `post.blocked`, `post.drafted`, `midcheck.passed|stale`, `post.sent`, `monitor.checked`, `post.stale`, `correction.threaded`, `notion.failed`, `error`. The dashboard's counters are computed from this log, not stored separately.

**Dogfooding found real bugs, in order, all visible in the commit history:**
1. The installed `anthropic` 1.5.0 SDK has no `temperature` parameter; the first live draft failed before any tokens were spent. Fixed by feature-detecting the signature.
2. Multi-word integration names ("GitHub webhooks") produced false blocks; fixed by expanding through the alias table. A lowercase word tagged as a symbol matched 25 files; fixed by ranking code over docs and capping receipts at 3 per entity.
3. After Approve, the Slack message was replaced by the approval line — deleting the text the human was meant to copy. Fixed to preserve text and receipts.
4. The staleness monitor correctly detected a renamed function (`post.stale` event with the removed symbol and commit) but the Notion mirror used a local row id where Notion expects a page id, and the error path overwrote the status. Detection was right; the mirror was wrong. See the commit log for the fix.

The substring guard fired on real drafts (`entity_dropped` for `cdl/app.py`, `cdl/monitor.py`) — the model named files the sentence did not mention, and the guard removed them before any verdict.

# Where the LLM is and isn't

Anthropic drafts 3–6 first-person sentences and extracts files, symbols, and integrations through forced `tool_use` JSON schemas. It never decides a verdict. The substring guard removes any extracted entity not literally present in the sentence (files may also match by basename) and logs each drop. `ANTHROPIC_MODEL` controls the model, and each call records model, input/output tokens, duration, and failures in SQLite/stdout. The matcher is pure Python and has zero network or LLM imports.
The anthropic 1.5.0 SDK removed the `temperature` parameter from `messages.create`; the client feature-detects it and otherwise runs at the SDK default. Cost for the whole build night, including every failed attempt and the eval run, was under $2.

# Failure handling

| Failure | Shipped behavior |
| --- | --- |
| Bad webhook/Slack signature | Reject with 401 and log an error event. |
| GitHub compare failure | Retry transient failures, then log `compare.failed` and keep the affected flow visible as Errored. |
| Missing GitHub patch | Keep the file receipt but mark unavailable-symbol claims unsupported with an explanatory suffix. |
| Malformed or overlong LLM tool input | Retry once with parse/length guidance; truncate a second overlong response to leading sentences and log `draft.truncated`, while other invalid input raises `LLMError` and persists an Errored row. |
| Notion or Slack write failure | Keep SQLite state and mark the affected post Errored with component and reason; external resync is an explicit follow-up operation. |
| Duplicate delivery/action | Use GitHub delivery ID or Slack action timestamp as an idempotency key. |
| Code changes before approval | Re-compare against HEAD; changed evidence becomes Stale and is never approved. |
| Missing configuration | `python -m cdl serve` refuses to start and names every missing variable; the Notion schema is asserted at boot and names every missing property. |

# What it doesn't do

The check is lexical, not semantic: a sentence can name the right file and still mischaracterise what changed inside it, and an integration name alone is a weak receipt. It tracks drift from this repository only, not regulations or third-party world state. The shipped scope is one repository, one channel, and one operator. Posting to X uses the web intent (human presses Post); the paid write API is deliberately not used. There is no multi-repo/auth layer, general hallucination detector, or Notion Worker.

# Architecture

One synchronous Python process runs FastAPI behind Caddy on a 1 GB VM. A GitHub push is verified and acknowledged, then a background task compares the range, monitors Sent evidence, reads Ready Notion notes, calls Anthropic for draft/extraction only, applies the deterministic gate, mirrors the result to Notion, and waits for Slack approval. SQLite is the audit source for the Jinja dashboard.

```text
GitHub push ──webhook──▶ /webhook/github ──202──▶ background pipeline
                                                     │
                                  ┌──────────────────┴──────────────────┐
                                  │ monitor → Stale/correction           │
                                  │ note → compare → draft → extract    │
                                  │ → claim_vs_diff → Notion → Slack     │
                                  └──────────────────────────────────────┘
```
