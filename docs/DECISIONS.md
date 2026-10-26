# Decisions

Newest first. Who decided, when, and why in one line.

- **2026-10-26 · Akash · Submit CDL to BharatCode Build League Round 1.** Old repos are allowed
  (Discord answer, 22 Oct 2026); the repo needs `BharatCode.txt` in the root and must work with
  the BharatCode API.
- **2026-10-26 · Akash · Work on branch `bharatcode`; keep the September submission as branch
  `lemma-hackathon`.** The form wants the main repo link, so `bharatcode` is merged into `main`
  before submitting.
- **2026-10-26 · Claude · BharatCode is the default provider; Anthropic stays behind
  `LLM_PROVIDER`.** Fewer moving parts than removing the Anthropic path, and the September demo
  stays reproducible.
- **2026-10-26 · Claude · Raw HTTPS (`httpx`) for BharatCode, not the OpenAI SDK.** Matches how
  the repo already calls GitHub and Notion, and adds no dependency.
- **2026-10-26 · Claude · Chat Completions, not Responses.** Both are documented; Chat Completions
  has forced `tool_choice` with a named function, which is exactly the existing tool contract.
- **2026-10-26 · Claude · `reasoning_effort: none` by default.** BharatCode counts reasoning
  against the output budget; extraction and short drafts do not need it. Configurable with
  `BHARATCODE_REASONING_EFFORT`.
- **2026-10-26 · Claude · Add a local CLI (`cdl check` / `cdl draft`).** Voters and judges can
  try it with one key instead of a GitHub webhook, two Notion databases and a Slack app.
- **2026-10-26 · Claude · CLI events go to `<repo>/.git/cdl/events.sqlite`.** Logged like the
  server, per repository, never committed, no clutter in the user's working tree.
