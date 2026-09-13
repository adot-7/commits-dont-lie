# Commits Don't Lie

*Shakira knows hips don't lie. We checked — neither do commits.*

> Every build-in-public tool drafts a post that sounds like you. None of them check whether
> it's true. Ours does — and it keeps checking after you hit send.

**▶ Demo (2 min):** https://youtu.be/zolODgOZ-uM · **Live:** https://commitsdontlie.akashparashar.dev ·
**Reliability brief:** [`BRIEF.md`](BRIEF.md)

Built solo in one night at the Multi-App AI Agent Hackathon (Lemma × Comma Capital, 13 Sep 2026).
Every post on the live dashboard was generated from this repository's own commits during the build.

---

## 01 · Project overview

**The problem.** Developers who build in public write "here's what I shipped" posts from memory
and intent. Tools that automate this draft from commits and optimise for *voice* — none of them
check whether the draft is *true*. A note that says "wired the dashboard tonight" becomes a post
even when the dashboard commit never landed. And once a post is out, nobody re-checks it when the
code it described gets deleted next week.

**What we built.** An agent that reads your GitHub commits and your Notion dev notes, drafts a
build-update post in your voice, and **refuses to publish any sentence it cannot point to in the
diff**. Approved drafts go to Slack for one-tap approval and a hand-off to X. After publishing,
**every future push re-checks every published post**: if the code a post cited is removed or
renamed, the post is flagged *Stale* and a correction is threaded under the original message.

**The check, in one sentence:**

> Every file, function, or integration a sentence names must appear in the diff — or the
> sentence doesn't ship.

| Verdict | Meaning |
|---|---|
| ✅ SUPPORTED | every named thing was found in the diff; receipts (file:line) attached |
| ⛔ UNSUPPORTED | a named thing is missing from the diff → **post blocked**, reason shown |
| ❔ UNVERIFIABLE | names nothing checkable (pure voice) → allowed, max one per post, labelled |

**How it decides.** The LLM does two jobs only: it *drafts* sentences and it *extracts* the
files/symbols/integrations each sentence names. A deterministic Python function
(`cdl/grounding/check.py::claim_vs_diff`) then matches those entities against the actual diff.
The model never emits a verdict. A substring guard drops any extracted entity that isn't literally
in the sentence, so the model can't smuggle in names that happen to be in the diff.

**One function, two call sites.** The same matcher runs at the pre-publish gate and, inverted,
in the post-publish monitor (`check_staleness`): did this new diff *remove* a receipt an
already-published post depends on?

## 02 · External apps used

| App | Role | How |
|---|---|---|
| **GitHub** | Source of truth. Push webhook triggers every run; the compare API supplies the diff with line numbers. | Webhook with HMAC-SHA256 verification; REST `compare` endpoint; fine-grained PAT (read-only). |
| **Notion** | Input *and* output. Your dev notes (what you *meant*) are read from a Notes database; every post is mirrored to a Posts database with status, receipts, and a link to the correction. | REST API version `2025-09-03` (data sources); schema asserted at boot. |
| **Slack** | Human approval. Drafts arrive with receipts and Approve/Reject buttons; approval re-checks against HEAD; corrections are threaded under the original message. | Bot token + signed interactions endpoint; Block Kit; `chat.update`; `thread_ts`. |
| **Anthropic** | Drafting and entity extraction only, via forced `tool_use` JSON schemas. | Claude Sonnet; never decides a verdict. |
| **X** | Hand-off. After approval, a "Post on X" button opens the compose box prefilled via the web intent — a human presses Post. | No paid write API used, deliberately. |

## 03 · Setup instructions

Runs as one Python 3.12 process (FastAPI) behind Caddy on a 1 GB VM. No Docker, no queue.

```bash
git clone https://github.com/adot-7/commits-dont-lie.git && cd commits-dont-lie
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill every value; no inline comments (systemd EnvironmentFile)
```

1. **GitHub** — fine-grained PAT (Contents + Metadata, read-only) → `GITHUB_TOKEN`. Generate
   `GITHUB_WEBHOOK_SECRET` (`openssl rand -hex 32`). After deploy: repo → Settings → Webhooks →
   payload URL `https://<host>/webhook/github`, JSON, that secret, push events only.
2. **Notion** — internal integration → `NOTION_TOKEN`. Create two databases under one page and
   connect the integration to the page:
   - `Notes`: `Name` (title), `Status` (select: Writing / Ready / Drafted), `Created` (created time)
   - `Posts`: `Name`, `Status` (select: Blocked / Draft / Sent / Rejected / Stale / Correction / Errored),
     `Post Text`, `Evidence`, `Commit Range`, `Head SHA`, `Slack TS` (text), `Superseded By`
     (relation → Posts), `Note` (relation → Notes), `Dashboard` (URL)
   - Put the two database IDs in `.env`, then `python -m cdl resolve-notion-ids` → paste the two
     data-source IDs. The app asserts this schema at boot and names anything missing.
3. **Slack** — app with scopes `chat:write`, `chat:write.public` → `SLACK_BOT_TOKEN`; Basic
   Information → Signing Secret → `SLACK_SIGNING_SECRET`; invite the bot to a channel →
   `SLACK_CHANNEL_ID`. Interactivity **on**, Socket Mode **off**, Request URL
   `https://<host>/slack/interactions`.
4. **Anthropic** — `ANTHROPIC_API_KEY`; `python -m cdl models` to confirm `ANTHROPIC_MODEL`.
5. `ADMIN_TOKEN` (`openssl rand -hex 16`) protects `POST /draft-now`. `FIRST_SHA` sets the base
   of the first post's range. Optional `style/examples.md` holds past posts for voice.

**Run:** `python -m cdl serve` (validates config, asserts Notion schema, then serves on
127.0.0.1:8000). Production files: `deploy/cdl.service`, `deploy/Caddyfile`, `deploy/deploy.sh`;
full VM runbook in [`docs/07-DEPLOY.md`](docs/07-DEPLOY.md).

**Use:** write a note in Notion → set `Ready` → push (or `POST /draft-now`). Blocked posts say
why and leave the note `Ready`; passing drafts arrive in Slack with receipts. Approve → Sent →
"Post on X". Every later push re-checks every Sent post.

## 04 · Reliability testing

**Show how you know it works** was the brief; this is how.

- **Offline test suite** — `make test`. Covers the diff parser (hunk-header line numbers), every
  verdict rule (file / symbol / integration matching, alias expansion, evidence ranking and cap,
  UNVERIFIABLE boundary), every staleness rule (removed file, renamed file, net-removed symbol,
  *moved* symbol is not stale, body edit is not stale), the substring guard, `tool_use` validation,
  the publish gate, approval mid-check, corrections, dashboard rendering, and the eval harness.
- **Hand-labelled evaluation set** — [`eval/cases.jsonl`](eval/cases.jsonl): 30 sentences
  written in the author's voice against 8 real commits from this night — for each commit one true
  claim, one plausible false claim naming code the commit did not touch, one pure-voice sentence —
  plus 6 adversarial cases (real file in the wrong range, near-miss filename, multi-word
  integration, two entities with one missing, decoy integration, pure voice). `make eval` runs the
  full production path (extract → guard → match) and writes a 3×3 confusion matrix with per-class
  precision/recall to `eval/report.md`, rendered live at `/eval`.
- **Live event log** — every external call and every verdict is an append-only event in SQLite and
  a JSON line on stdout: `push.received`, `compare.fetched`, `llm.call` (tokens, ms), `verdict`
  (status, evidence, missing, reason), `entity_dropped`, `post.blocked`, `post.drafted`,
  `midcheck.passed|stale`, `post.sent`, `monitor.checked`, `post.stale`, `correction.threaded`,
  `notion.failed`, `error`. The dashboard's counters are computed from this log. Nothing fails
  silently: a failed external write marks the post `Errored` with the component and reason.
- **Dogfooding** — the tool was pointed at its own repository during the build. The posts on the
  live dashboard are real; the blocked sentences were real over-claims from real notes.
- **Failure handling** — bad signatures → 401 and logged; GitHub compare retried then `Errored`;
  missing patches make symbol claims unsupported with an explicit suffix; malformed LLM output
  retried once with the error, then `Errored`; duplicate webhook deliveries and Slack actions are
  idempotent; code that changes between draft and Approve is caught by the mid-check and never
  sent. The full table, as shipped, is in [`BRIEF.md`](BRIEF.md).
- **What it does not do** — the check is lexical, not semantic: a sentence can name the right
  file and still mischaracterise what changed inside it. Integration names alone are weak
  receipts. Drift is tracked for *this repository's* code only, not the outside world. Single
  repo, single channel, single operator. These are stated, not hidden.

## 05 · Demo video

**▶ https://youtu.be/zolODgOZ-uM** (2:00)

What it shows, in order: a real note over-claims → the gate blocks the sentence and names it →
the fixed draft passes with file:line receipts → Approve re-checks HEAD and hands off to X →
a post published earlier cited a function that was later renamed, and the monitor flagged it
Stale with a threaded correction.

---

Architecture, data contracts, integration call sheets, eval plan, and the deploy runbook are in
[`docs/`](docs/) and [`AGENTS.md`](AGENTS.md). MIT.
