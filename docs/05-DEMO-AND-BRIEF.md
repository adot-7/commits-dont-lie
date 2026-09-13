# 05 — Demo (2:00) and the reliability brief

## 1. Demo beats — record 03:45–04:15 IST, hard stop
Pre-state at 03:40: dashboard already shows 3–4 real posts from tonight (dogfooding, M5). A
Notion note written ~02:30 is `Ready` and contains one intent-tense sentence that the range
does not support (e.g. "eval page is live" before the eval commit). Slack `#build-log` open.

| # | ~sec | On screen | Say |
|---|---|---|---|
| 1 | 0–15 | Dashboard, 3 posts visible, verdict colours | "Commits Don't Lie writes your build updates from your commits and notes — and refuses to publish a sentence it can't point to in the diff. Every build-in-public tool drafts a post that sounds like you. None check if it's true." |
| 2 | 15–40 | Notion note (the real one) → terminal `git push` → Slack: **⛔ Blocked** with the reason → dashboard row with the sentence, `UNSUPPORTED`, "'eval page' does not appear in the 6 files changed in a1f2c3d..9e8d7c6" | "This is the note I wrote at 2:30. I said the eval page was live. It wasn't in the diff yet — so it never left the building. Not staged: this is tonight's repo." |
| 3 | 40–65 | Edit the note (delete that sentence) → push → Slack draft with receipts footer → click **Approve** → message updates ✅ → dashboard `Sent`; expand a sentence → `cdl/grounding/check.py:41 claim_vs_diff` | "Every sentence carries its receipt: file, line, function. The model writes; Python decides. Approve sends." |
| 4 | 65–85 | Scroll dashboard: earlier posts beside their commit ranges; `/eval` page: 14/15 | "These are the real posts from tonight. And this is how we know the checker works — a hand-labelled set, run on every change." |
| 5 | 85–115 | Terminal: `git mv cdl/grounding/check.py …` or rename `claim_vs_diff` → push → dashboard row flips **Stale**, Slack thread under the original post: "⚠️ Stale: claim_vs_diff was removed in 3b9c1e0" | "And it keeps checking. I just renamed the function post #2 cited. The post is now flagged stale and the correction is threaded under the original. Provenance that's only checked at publish time is a screenshot. This is monitoring." |
| 6 | 115–120 | Dashboard URL | "Live at commitsdontlie.akashparashar.dev. Three apps, one function, two call sites." |

Recording notes: `textual`-style large fonts not needed — browser at 125% zoom, terminal font
18pt. Record beats 2 and 5 as single takes; if beat 5 misfires, the cut order (`docs/06 §4`) says
drop it from the video, keep the mechanism in the README.

## 2. Reliability brief (≤ 1 page, `BRIEF.md` in repo root; README links it)
Use these headings, in this order, same language as the video:
1. **What it does** — the one-sentence check; three verdicts; two call sites.
2. **How we know it works** — `make eval` result (N/15, confusion matrix), unit-test count,
   production counters from the dashboard (pushes, posts, verdict totals, entities dropped).
3. **Where the LLM is and isn't** — drafts and extracts only; deterministic matcher decides;
   substring guard; `tool_use` schemas; model id; tokens and cost for the night.
4. **Failure handling** — the table from `docs/01 §Failure`, as shipped (edit to match reality).
5. **What it doesn't do** — lexical not semantic; own-repo drift only; no X posting; Notion
   Workers deliberately not used; single repo/channel.
6. **Architecture** — one paragraph + the ASCII diagram from `docs/01`.

## 3. README skeleton
Title · one-line positioning · 60-second GIF or screenshot of dashboard · "The check" (one
sentence, three verdicts) · "Two call sites" · Architecture diagram · Setup (env vars, Notion
schema, Slack app, webhook) · Eval (`make eval`) · Limitations & future work (X posting,
multi-repo, semantic checks, world-state drift) · link to `BRIEF.md` and the live URL.
