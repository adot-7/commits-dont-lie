# 06 — Milestones, issues, timeboxes, cut order

Clock: IST. Deadline **04:30**. Recording starts **03:45** no matter what.
Issues are labelled `M0`…`M5` and titled `[M<n>] …`. Work top-down. Acceptance criteria are in
each issue; the spec is the referenced doc section.

## M0 — Walking skeleton (23:40 → 00:20) — the URLs must exist before logic does
- [M0] Repo scaffold: `cdl/` package, `config.py`, `models.py`, `store.py` schema, `Makefile`,
  `requirements.txt`, `pytest` smoke test. (`docs/01`, `docs/02 §1–5`)
- [M0] FastAPI app with `/healthz`, `POST /webhook/github` (HMAC verify → persist push → 202,
  log payload), `POST /slack/interactions` (signature verify → 200, log), `GET /` placeholder.
  (`docs/01 §Request flow`, `docs/03 §1, §3`)
- [M0] Deploy to the VM per `docs/07 §1–4`; Caddy TLS on `commitsdontlie.akashparashar.dev`;
  `curl https://…/healthz` → 200. **Human:** DNS A record, security list, then webhook +
  Slack Interactivity URL (`docs/07 §5, §7`).

## M1 — Ingest (00:20 → 00:50)
- [M1] `github_client.compare()` → `DiffContext` with hunk-header line numbers; `dump-compare`
  CLI; fixture captured from a real range. (`docs/02 §1`, `docs/03 §1`)
- [M1] `notion_client`: `resolve-notion-ids` CLI, `ready_notes()`, `read_note_body()`, boot-time
  schema assertion. (`docs/03 §2`, `docs/02 §7`)

## M2 — Grounding core (00:50 → 01:50) — most of the night's value; protect it
- [M2] `grounding/check.py`: `claim_vs_diff` + `check_staleness` + alias table, pure functions.
  (`docs/02 §4`)
- [M2] `tests/test_check.py` covering every bullet in `docs/04 §1`. `make test` green.
- [M2] `llm.py`: `draft()` and `extract_entities()` via `tool_use` schemas; substring filter;
  `llm.call` events with usage. (`docs/02 §8`, `docs/03 §4`)

## M3 — Draft → gate → approve (01:50 → 02:35)
- [M3] `drafter.py` + `pipeline.handle_push()` drafting branch: ready note → range since last
  post → draft → extract → verdicts → gate → SQLite → Notion row → Slack (blocked notice or
  draft with buttons) → note `Drafted`. (`docs/01 §Request flow — push` steps 4–5)
- [M3] `approval.py`: approve/reject handling, **mid-check against HEAD**, `chat.update`,
  Notion status, note back to `Ready` on reject. (`docs/01 §Request flow — Slack`)
- [M3] `draft-now` CLI + `POST /draft-now` (ADMIN_TOKEN) for demo control.

## M4 — Monitor + dashboard (02:35 → 03:10)
- [M4] `monitor.py`: staleness pass on every push over `Sent` posts → `Stale`, correction row,
  Notion update, Slack thread reply. (`docs/01 §Request flow — push` step 3, `docs/02 §4 staleness`)
- [M4] Dashboard: `/` (posts table, counters), `/posts/{id}` (sentences, verdicts, evidence with
  GitHub blob links `https://github.com/{repo}/blob/{head}/{path}#L{n}`), `/eval` (renders
  `eval/report.json` if present). Jinja + one CSS file. **Timebox 30 min.**

## M5 — Dogfood, eval, docs (03:10 → 03:45)
- [M5] Human writes a real Notion note → `Ready`; push; iterate until 3–4 posts are `Sent`.
  Plant the beat-2 note (`docs/05 §1`).
- [M5] Human writes `eval/cases.jsonl` (15 lines, `docs/04 §2`); agent runs `make eval`;
  report committed; `/eval` renders it.
- [M5] `README.md` + `BRIEF.md` per `docs/05 §2–3`. `.env.example` complete. Secret scan.

## 03:45 → 04:15 record · 04:15 → 04:30 submit (URL, repo, video, brief)

## 4. Cut order — binding, top-down
1. `/eval` page → keep `eval/report.md` in repo, link from README.
2. Slack threaded correction → keep Notion `Stale` + dashboard flag.
3. Beat 5 live in video → keep the mechanism, describe it, show a pre-recorded or static Stale row.
4. Notion `Posts` mirror → SQLite + dashboard remain the log; Notion stays as *input* (still 3 apps).
5. Reject button → approve only.
**Never cut:** the pre-publish gate with evidence, the Slack approve step, the dashboard `/`.

## 5. De-scope triggers
- 01:50 and `claim_vs_diff` tests not green → drop integration aliases and basename matching;
  ship file + symbol only.
- 02:35 and Slack buttons not working → fall back to emoji-reaction polling on `draft-now`
  (`reactions.get`); note it in BRIEF honestly.
- 03:10 and monitor not working → cut order item 3.
