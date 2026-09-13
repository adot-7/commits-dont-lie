# Commits Don't Lie

> Every build-in-public tool drafts a post that sounds like you. None of them check whether
> it's true. Ours does — and it keeps checking after you hit send.

An agent that reads your **GitHub** commits and **Notion** dev notes, drafts a build-update
post, and **refuses to publish any sentence it cannot point to in the diff**. Approved drafts
go to **Slack** for one-tap send. After publishing, every future push re-checks every claim:
if the code a post cited is removed, the post is flagged **Stale** and a correction is threaded
under the original message.

Live: https://commitsdontlie.akashparashar.dev · Built solo at the Multi-App AI Agent
Hackathon (Lemma × Comma Capital), 13 Sep 2026.

## The check
**Every file, function, or integration a sentence names must appear in the diff — or the
sentence doesn't ship.**

| Verdict | Meaning |
|---|---|
| ✅ SUPPORTED | every named thing found; receipts (file:line) attached |
| ⛔ UNSUPPORTED | a named thing is missing from the diff → post blocked |
| ❔ UNVERIFIABLE | names nothing checkable → rewrite or drop |

The LLM writes and extracts. A deterministic matcher decides. One function, two call sites:
the pre-publish gate and the post-publish monitor.

## Status
Under construction tonight. Spec lives in [`AGENTS.md`](AGENTS.md) and [`docs/`](docs/).
Reliability brief: `BRIEF.md` (end of night).
