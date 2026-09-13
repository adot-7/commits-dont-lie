# 03 — Integrations: exact calls

Use `httpx.Client(timeout=20)` for GitHub and Notion. Use `slack_sdk.WebClient` for Slack.
Use `anthropic.Anthropic()` for the LLM. Every call logs an `events` row (`docs/02 §6`).

Why raw HTTPS for Notion: the API split databases into *data sources* in version `2025-09-03`
and SDK support for that version varies; two `httpx` calls are fewer unknowns than an SDK
whose version we cannot audit tonight. Why raw HTTPS for GitHub: we need exactly two endpoints.

## 1. GitHub
Auth: fine-grained PAT, repository `adot-7/commits-dont-lie`, permissions **Contents: read,
Metadata: read**. Header `Authorization: Bearer $GITHUB_TOKEN`, `Accept: application/vnd.github+json`,
`X-GitHub-Api-Version: 2022-11-28`.

- **Compare** — `GET https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}`
  → `commits[]` (`sha`, `commit.message`, `commit.author.date`, `html_url`),
  `files[]` (`filename`, `status`, `previous_filename`, `patch`, `additions`, `deletions`).
  Caps: ~300 files; `patch` omitted for very large/binary files → set `truncated=True`.
- **Single commit** (fallback / `replay`) — `GET /repos/{owner}/{repo}/commits/{sha}` same `files[]` shape.
- **Webhook** (human sets up once the URL is live; `docs/07 §5`): repo → Settings → Webhooks →
  Add. Payload URL `https://commitsdontlie.akashparashar.dev/webhook/github`, content type
  `application/json`, secret `$GITHUB_WEBHOOK_SECRET` (generate: `openssl rand -hex 32`),
  event: **Just the push event**. Verify: `hmac.compare_digest("sha256=" +
  hmac.new(secret, raw_body, sha256).hexdigest(), request.headers["X-Hub-Signature-256"])`.
  Payload fields used: `ref`, `before`, `after`, `commits[].id`, `repository.full_name`,
  header `X-GitHub-Delivery`.

## 2. Notion (API version `2025-09-03`)
Auth: internal integration secret. Headers on every call:
`Authorization: Bearer $NOTION_TOKEN`, `Notion-Version: 2025-09-03`, `Content-Type: application/json`.
Human setup: `docs/07 §6`. The human gives us two **database IDs** (from the URLs); we resolve
their **data source IDs** once:

- **Resolve** — `GET https://api.notion.com/v1/databases/{database_id}` → `data_sources: [{id, name}]`.
  Take `[0].id`. CLI `python -m cdl resolve-notion-ids` prints both; human pastes into `.env`
  as `NOTION_NOTES_DS_ID`, `NOTION_POSTS_DS_ID`.
- **Query ready notes** — `POST /v1/data_sources/{NOTION_NOTES_DS_ID}/query`
  body `{"filter": {"property": "Status", "select": {"equals": "Ready"}},
  "sorts": [{"timestamp": "created_time", "direction": "ascending"}]}` → `results[]` pages;
  `properties.Name.title[0].plain_text`, `id`.
- **Read note body** — `GET /v1/blocks/{page_id}/children?page_size=100` → join
  `paragraph.rich_text[].plain_text` (also `bulleted_list_item`, `heading_*`) with newlines.
- **Create post row** — `POST /v1/pages` body
  `{"parent": {"type": "data_source_id", "data_source_id": NOTION_POSTS_DS_ID},
  "properties": {"Name": {"title": [{"text": {"content": ...}}]},
  "Status": {"select": {"name": "Draft"}},
  "Post Text": {"rich_text": [{"text": {"content": chunk}} for chunk in chunks(text, 2000)]},
  "Evidence": {"rich_text": [...]}, "Commit Range": {"rich_text": [...]},
  "Head SHA": {"rich_text": [...]}, "Dashboard": {"url": ...},
  "Note": {"relation": [{"id": note_page_id}]}}}` → `id`.
- **Update status / ts / superseded** — `PATCH /v1/pages/{page_id}` body
  `{"properties": {"Status": {"select": {"name": "Sent"}}, "Slack TS": {"rich_text": [...]},
  "Superseded By": {"relation": [{"id": correction_page_id}]}}}`.
- **Mark note** — `PATCH /v1/pages/{note_page_id}` `{"properties": {"Status": {"select": {"name": "Drafted"}}}}`.
- Rate limit ~3 req/s: fine. Errors: 400 with `message` — log it whole.
- Gotcha: property names are case-sensitive and must match the human-created schema exactly
  (`docs/02 §7`). On boot, `GET /v1/data_sources/{id}` and assert required properties exist;
  fail loudly listing the missing ones.

## 3. Slack
App: `docs/07 §7`. Bot token `xoxb-…` (`SLACK_BOT_TOKEN`), signing secret
(`SLACK_SIGNING_SECRET`), channel id `C…` (`SLACK_CHANNEL_ID`). Scopes: `chat:write`,
`chat:write.public` (so no invite needed; invite anyway).

- **Post draft** — `client.chat_postMessage(channel, text=<fallback>, blocks=[...])` → `ts`.
  Blocks: `section` (post text) · `context` (evidence lines, `mrkdwn`) · `actions` with two
  `button`s: `action_id="approve"` (style primary) and `action_id="reject"` (style danger),
  `value=str(post_id)`.
- **Blocked notice** — same, no `actions`; header "⛔ Blocked — N sentence(s) unsupported", each
  with its reason.
- **After approve/reject** — `client.chat_update(channel, ts, text, blocks=<no actions>)`.
- **Correction** — `client.chat_postMessage(channel, thread_ts=post.slack_ts, text="⚠️ Stale: …")`.
- **Interactions endpoint** `POST /slack/interactions`: form-encoded `payload=<json>`. Verify with
  `SignatureVerifier(SLACK_SIGNING_SECRET).is_valid_request(body_bytes, headers)`. Return `200`
  empty **within 3 s**; do the work in a `BackgroundTask`. Payload: `actions[0].action_id`,
  `actions[0].value`, `user.username`, `message.ts`, `channel.id`, `actions[0].action_ts`
  (idempotency key).
- Interactivity Request URL: `https://commitsdontlie.akashparashar.dev/slack/interactions`
  (set after `docs/07 §4` is live; Slack does not verify this URL on save).

## 4. Anthropic
`ANTHROPIC_API_KEY`; model from `ANTHROPIC_MODEL`, default `claude-sonnet-5`. If the API returns
`not_found` for the model id, run `python -m cdl models` (calls `client.models.list()`), pick the
newest Sonnet, set the env var. Use `client.messages.create(model, max_tokens=1024, system=...,
messages=[...], tools=[{name, input_schema}], tool_choice={"type": "tool", "name": ...})` and read
`response.content[0].input` — this yields schema-valid JSON without prose. Log `usage`.

## 5. Environment (`.env.example` is canonical)
```
APP_BASE_URL=https://commitsdontlie.akashparashar.dev
ADMIN_TOKEN=                # for POST /draft-now from the dashboard; openssl rand -hex 16
GITHUB_TOKEN=
GITHUB_WEBHOOK_SECRET=
GITHUB_REPO=adot-7/commits-dont-lie
NOTION_TOKEN=
NOTION_NOTES_DB_ID=
NOTION_POSTS_DB_ID=
NOTION_NOTES_DS_ID=         # from `python -m cdl resolve-notion-ids`
NOTION_POSTS_DS_ID=
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
SLACK_CHANNEL_ID=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5
DATABASE_PATH=data/cdl.sqlite
```
