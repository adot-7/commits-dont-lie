# AGENTS.md — Commits Don't Lie

You are a coding agent building **Commits Don't Lie** (CDL) for the Multi-App AI Agent
Hackathon (Lemma × Comma Capital, Sun 13 Sep 2026). Hard deadline: **04:30 IST / 16:00 PT**.
Solo human operator: Akash (`adot-7`). You write the code; he runs ops (tokens, DNS, Slack app,
Notion pages) and approves posts.

## Read order (do not skip)
1. `docs/00-CONTEXT.md` — what we are building and why; the pitch; scope fences.
2. `docs/01-ARCHITECTURE.md` — modules, request flow, state.
3. `docs/02-DATA-CONTRACTS.md` — **the `claim_vs_diff` interface**, dataclasses, verdict rules, events, Notion schema.
4. `docs/03-INTEGRATIONS.md` — exact GitHub / Notion / Slack / Anthropic calls, headers, versions.
5. `docs/06-MILESTONES.md` — the ordered issue list, timeboxes, cut order. Work issues top-down.
6. `docs/04-EVAL.md`, `docs/05-DEMO-AND-BRIEF.md`, `docs/07-DEPLOY.md` — when the relevant issue comes up.

GitHub issues are titled `[M<n>] …` and reference the doc section that specifies them. The doc
is the spec; the issue is the work item. If they disagree, the doc wins — fix the issue.

## Non-negotiable rules
1. **The LLM never decides a verdict.** It drafts sentences and extracts entities. A deterministic
   Python function (`grounding/check.py::claim_vs_diff`) decides SUPPORTED / UNSUPPORTED /
   UNVERIFIABLE by matching entities against the diff. Every verdict carries reproducible evidence.
   If you find yourself asking the model "is this claim supported?", stop — that is the bug.
2. **One function, two call sites.** `claim_vs_diff` runs at the pre-publish gate and (via
   `check_staleness`, which reuses the same matcher) on every later push against every Sent post.
   Do not fork the logic.
3. **Extracted entities must literally appear in the sentence.** After extraction, drop any
   file/symbol/integration string that is not a case-insensitive substring of the sentence text.
   This stops the model from inventing entities that happen to be in the diff.
4. **No secrets in git.** `.env` is gitignored; `.env.example` lists every variable. The repo is
   public. Run `git diff --cached | grep -iE 'xoxb|ntn_|secret_|sk-ant|ghp_|github_pat'` before
   every commit; abort on a hit.
5. **Commit messages are demo content.** CDL is dogfooded on this repo tonight; your commit
   messages and diffs are the raw material for the posts in the video. Write small, honest,
   descriptive commits (`feat(grounding): deterministic entity matcher with evidence lines`).
   Never squash the night into one commit.
6. **Fail loudly, log everything.** Every external call and every verdict appends a JSON line to
   the events table (`docs/02 §6`). Errors mark the affected post `Errored` with the reason —
   never silently skip a commit or a claim. Silent failure is the thing our judges sell against.
7. **Sync over async cleverness.** Single uvicorn worker, FastAPI `BackgroundTasks` for
   post-ack work, `httpx` sync client, SQLite. 1 GB RAM VM. No Celery, no Redis, no Docker.
8. **Timebox.** Each issue has a timebox in `docs/06`. When it expires, ship what works, open a
   follow-up issue, move on. The cut order in `docs/06 §4` is binding.
9. **Stack:** Python 3.12, FastAPI, uvicorn, httpx, `slack_sdk`, `anthropic`, Jinja2, sqlite3
   (stdlib), pytest. Raw HTTPS for GitHub and Notion (no SDK — see `docs/03` for why).
10. **Direct to `main`.** Solo repo, no PR ceremony. Reference the issue in the commit
    (`… (#12)`), close it when the acceptance criteria in the issue are met.

## Working conventions
- Package root: `cdl/`. Entry: `cdl/app.py` (FastAPI). CLI: `python -m cdl <cmd>`.
- Config: `cdl/config.py` reads env via `os.environ`; missing required var → hard fail at boot
  with the variable name.
- Tests: `tests/` with fixture diffs in `tests/fixtures/`. `make test` must pass before each
  push after M2. `make eval` runs `docs/04`.
- Every module has a 3–6 line docstring stating what it owns and what it must not do.
- Type hints everywhere; `dataclasses` for contracts in `cdl/models.py`.
- Log format: one JSON object per line to stdout **and** to the `events` table.

## When blocked
- API returns something the docs did not predict → log the raw response, write a minimal
  reproduction in the issue, pick the smallest workaround, keep moving.
- A decision not covered by the docs → choose the option with fewer moving parts, note it in
  `docs/DECISIONS.md` (create on first use) with a one-line why.
