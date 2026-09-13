# 00 — Context

## The event
Multi-App AI Agent Hackathon · Lemma × Comma Capital · virtual · Sun 13 Sep 2026.
Brief (verbatim from multiappagenthackathon.com): *"Build one useful, multi-step AI agent.
Connect it to at least three external apps. **Show how you know it works.**"*

Submit: working project or repository · two-minute demo · short system and reliability brief.
Judging: 30% technical execution · **25% reliability & evaluation** · 20% usefulness ·
15% originality · 10% demo clarity. Judges: founders of Lemma (agent trace auditing — "looks
like it worked but didn't"), Arga Labs (sandboxed API twins), Userlens. Judges said in the
kickoff they **prefer a front end and a production link**.

Timeline (IST): build 22:00 → 04:30 deadline. Awards ~05:00.

## What we are building
**Commits Don't Lie** — an agent that turns your GitHub commits + Notion dev notes into a
build-update post, and **refuses to publish any sentence it cannot point to in the diff**.
After publishing, it keeps checking: if a later commit removes the code a post cited, the post
is flagged **Stale** and a correction is threaded under the original Slack message.

Three external apps, each doing real work:
- **GitHub** — ground truth. Push webhook triggers; compare API supplies the diff.
- **Notion** — input (your dev notes, what you *meant*) and output (the post log with evidence).
- **Slack** — human approval with buttons; corrections threaded under the original post.

Plus a read-only **dashboard** at `https://commitsdontlie.akashparashar.dev` showing every
post, every sentence, its verdict, and its receipt. This is the production link.

## The one-sentence check (say it exactly this way everywhere)
> Every file, function, or integration a sentence names must appear in the diff — or the
> sentence doesn't ship.

Three verdicts: **SUPPORTED** (every named thing found, receipts attached) ·
**UNSUPPORTED** (a named thing is missing → post blocked) · **UNVERIFIABLE** (names nothing
checkable → sentence must be rewritten or dropped before the post can ship).

## Why this shape (the product logic)
- Notes are written in the tense of intent ("dashboard done too"). Diffs are what actually
  happened. Every commit→post tool blends them and publishes the blend. We check the blend
  against the diff, sentence by sentence, and show the receipt.
- The LLM only *writes* and *extracts*. A deterministic matcher *decides*. That makes every
  verdict reproducible, auditable, and cheap to evaluate — and it is the honest answer to
  "show how you know it works": a hand-labelled eval set, run by `make eval`, reported on the
  dashboard.
- The check does not expire. Provenance that is only checked at publish time is a screenshot;
  provenance that is re-checked on every push is monitoring. Lemma's whole company is the
  second thing.

## Trigger model
Two things happen on every push; only one talks to you.
1. **Always, silently — the monitor.** Re-check every `Sent` post's evidence against the new
   diff. Evidence removed → `Stale` + threaded Slack correction.
2. **Only when you have something to say — the drafter.** If a Notion note is marked `Ready`,
   gather **all commits since the last post**, read the note, draft, check, gate. No note →
   no draft. Twenty refactor commits with no note produce zero noise.

## Scope fences (do not cross tonight)
- ❌ No X/Twitter posting (write API is a paid tier). Slack approval is the terminal action;
  the human copies the text out. README: future work.
- ❌ No Notion Workers (hosted runtime, Business/Enterprise plans). Plain Notion REST API.
- ❌ No general hallucination detection. The check is the one sentence above, nothing more.
- ❌ No world-state drift (regulations, third-party API changes). Only drift from *this repo*.
- ❌ No multi-repo, no auth/multi-user, no React. One repo, one channel, one Jinja page.

## Positioning line for the video
"Every build-in-public tool drafts a post that sounds like you. None of them check whether it's
true. Ours does — and it keeps checking after you hit send."

## Honest competitive note (for README; do not name unverified products)
Commit→post tools exist (e.g. OpenTweet) and optimise voice and cadence; none we found verify
claims against the diff before publishing or after. "Verify before you trust the model" is a
rising pattern in 2026 hackathons generally; our differentiation is the deterministic evidence
trail and the post-publish monitor, not the existence of a check.
