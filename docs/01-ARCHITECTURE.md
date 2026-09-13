# 01 — Architecture

## Shape
One Python process. FastAPI behind Caddy on an Oracle free-tier VM (1 vCPU, 1 GB). SQLite is
the working store and event log; Notion is the user-facing mirror; Slack is the approval
surface; GitHub is the source of truth. No queue, no workers, no Docker.

```
GitHub push ──webhook──▶ /webhook/github ──202──┐
                                                 ▼ BackgroundTask
                                   ┌──────────────────────────────┐
                                   │ pipeline.handle_push(payload)│
                                   │  1. verify + persist push    │
                                   │  2. monitor.run(diff)  ──────┼──▶ Notion: Stale, Slack: thread reply
                                   │  3. drafter.maybe_draft()    │
                                   │     ├ notion.ready_notes()   │
                                   │     ├ github.compare(range)  │
                                   │     ├ llm.draft()            │
                                   │     ├ llm.extract_entities() │
                                   │     ├ check.claim_vs_diff()  │  ◀── deterministic
                                   │     └ gate → Notion + Slack  │
                                   └──────────────────────────────┘
Slack button ──▶ /slack/interactions ──200──▶ BackgroundTask: approval.handle()
                                                 └ mid-check re-verify vs HEAD → Sent | Stale
Browser ──▶ /  /posts/{id}  /eval  /healthz      (Jinja, reads SQLite only)
```

## Modules (`cdl/`)
| Module | Owns | Must not |
|---|---|---|
| `app.py` | FastAPI app, routes, background task dispatch, signature verification | contain business logic |
| `config.py` | env loading, required-var validation, constants (model id, channel, repo) | read env anywhere else |
| `models.py` | all dataclasses from `docs/02` | import anything but stdlib |
| `store.py` | SQLite schema + CRUD + `events` append | know about Notion/Slack |
| `github_client.py` | `compare(base, head)` → `DiffContext`; webhook HMAC check | parse patches beyond `+`/`-` lines |
| `notion_client.py` | notes query, read note body, posts create/update, data-source id resolution | hold verdict logic |
| `slack_client.py` | post draft w/ buttons, `chat.update`, thread reply, signature verify | decide anything |
| `llm.py` | `draft(note, diff) -> list[Sentence]`, `extract_entities(sentence) -> Entities` | emit verdicts |
| `grounding/check.py` | **`claim_vs_diff`**, `check_staleness`, matcher helpers | call the network |
| `pipeline.py` | `handle_push`, orchestrates monitor → drafter → gate | talk to APIs directly (uses clients) |
| `monitor.py` | staleness pass over Sent posts | draft |
| `drafter.py` | note → draft → extract → check → gate → mirror to Notion/Slack | match entities itself |
| `approval.py` | Slack button handling, mid-check, Sent/Rejected transitions | skip the mid-check |
| `eval/run_eval.py` | `docs/04` harness | be imported by the app |
| `templates/` | `base.html`, `index.html`, `post.html`, `eval.html` | |
| `__main__.py` | CLI: `serve`, `draft-now`, `replay <push_id>`, `eval`, `resolve-notion-ids` | |

## Request flow — push
1. `POST /webhook/github`: verify `X-Hub-Signature-256` (HMAC-SHA256 of raw body with
   `GITHUB_WEBHOOK_SECRET`). Reject 401 on mismatch. Ignore non-`push` events and pushes not
   to `refs/heads/main` (200, logged). Persist `pushes` row (`delivery_id` from
   `X-GitHub-Delivery` is the idempotency key — duplicate → 200, no work). Return **202**
   immediately; GitHub times out at 10 s.
2. Background: `github.compare(before, after)` → `DiffContext` (files, added/removed lines,
   commit messages). If `before` is all zeros (new branch) use the first commit's parent.
3. `monitor.run(diff)`: for every post with status `Sent`, `check_staleness(post.evidence,
   diff)`. Stale → store, Notion `Status=Stale`, Slack `chat.postMessage(thread_ts=post.slack_ts)`
   with the removed evidence, create a `Correction` row linked via `Superseded By`.
4. `drafter.maybe_draft()`: `notion.ready_notes()`; none → done. Else range =
   `store.last_posted_sha() or first commit tonight` … `after`. `github.compare(range)`.
   `llm.draft()` → sentences with `source ∈ {notes, commits, both}`. For each sentence
   `llm.extract_entities()` → substring filter → `claim_vs_diff()`.
5. Gate: any `UNSUPPORTED` or `UNVERIFIABLE` → status `Blocked`; Notion row with verdicts;
   Slack message (no buttons) listing blocked sentences and reasons; note stays `Ready`.
   All `SUPPORTED` → status `Draft`; Notion row with Evidence; Slack message with post text,
   evidence footer, **Approve / Reject** buttons; store `slack_ts`; note → `Drafted`.

## Request flow — Slack interaction
1. `POST /slack/interactions`: verify `X-Slack-Signature` (`slack_sdk.signature.SignatureVerifier`).
   Parse `payload` form field. Return **200 empty body within 3 s**. Background the rest.
2. `approve`: mid-check — `github.compare(post.head_sha, HEAD)`; if any evidence removed →
   status `Stale` before send, `chat.update` message "⚠️ Not sent: evidence changed since draft",
   Notion `Stale`. Else status `Sent`, `sent_at`, `chat.update` removing buttons and appending
   "✅ Approved — copy & post", Notion `Sent`.
3. `reject`: status `Rejected`, `chat.update`, Notion `Rejected`, note back to `Ready`.

## State
- **SQLite** (`data/cdl.sqlite`): `pushes`, `posts`, `sentences`, `evidence`, `events`,
  `notes_seen`. Schema in `docs/02 §5`. Dashboard reads only this.
- **Notion**: mirror of `posts` (+ the human-authored `Notes`). Written through on every
  status change; a Notion failure marks the post `Errored(notion)` but never blocks the Slack
  step from having happened — order of writes is Notion(Draft) → Slack → Notion(ts). See §Failure.
- **Idempotency keys**: push `delivery_id`; post `(note_id, head_sha)`; Slack action `action_ts`.

## Failure handling (this is the reliability story — implement it, then write it up)
| Failure | Behaviour |
|---|---|
| Webhook bad signature | 401, event logged, nothing else |
| GitHub compare 4xx/5xx | retry ×2 with backoff; then post `Errored(github)`, event logged |
| Compare returns file with `patch=None` (binary/huge) | file listed, no lines; entities can only match by path; sentence naming symbols inside → UNSUPPORTED with reason "patch unavailable" |
| LLM malformed JSON | one retry with the parse error appended; then `Errored(llm)` |
| LLM extracts entity not in sentence | dropped by substring filter; event `entity_dropped` |
| Notion write fails after Slack send | post keeps `slack_ts` in SQLite; `Errored(notion)`; `python -m cdl resync` retries |
| Slack post fails | post stays `Draft` in SQLite/Notion with `Errored(slack)`; `resync` retries |
| Duplicate webhook delivery | recognised by `delivery_id`, 200, no-op |
| Repo changed between draft and approve | mid-check → `Stale` before send (never sends stale text) |

## LLM usage (two calls per draft, zero per verdict)
- Model: `ANTHROPIC_MODEL` env, default `claude-sonnet-5` (override if unavailable —
  see `docs/03 §4`). `max_tokens` 1024. Temperature 0.3 for draft, 0 for extraction.
- Cost estimate: ~6–10k input tokens per draft (patches truncated to 4k chars/file, 20 files),
  ~600 output. ≈ $0.05–0.10 per draft. Whole night incl. tests: < $3.
