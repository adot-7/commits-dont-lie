# 02 — Data contracts

All dataclasses live in `cdl/models.py`. Stdlib only.

## 1. Diff side

```python
@dataclass
class FileChange:
    path: str                      # new path
    status: Literal["added","modified","removed","renamed"]
    previous_path: str | None      # renamed only
    patch: str | None              # unified diff text; None if GitHub omitted it
    added: list[tuple[int, str]]   # (new_line_no, text) for '+' lines, no leading '+'
    removed: list[tuple[int, str]] # (old_line_no, text) for '-' lines

@dataclass
class DiffContext:
    repo: str                      # "adot-7/commits-dont-lie"
    base_sha: str
    head_sha: str
    commits: list[CommitMeta]      # sha, message, author, timestamp, url
    files: list[FileChange]
    truncated: bool                # GitHub capped files (>300) or omitted patches
```

`github_client.compare()` builds this from `GET /repos/{o}/{r}/compare/{base}...{head}`.
Line numbers come from parsing `@@ -a,b +c,d @@` hunk headers; keep it simple and tested.

## 2. Claim side

```python
Source = Literal["notes", "commits", "both"]

@dataclass
class Sentence:
    idx: int
    text: str
    source: Source                 # where the drafter says this came from

@dataclass
class Entities:
    files: list[str]               # "cdl/slack_client.py", "slack_client.py", "Caddyfile"
    symbols: list[str]             # "claim_vs_diff", "SignatureVerifier", "handle_push"
    integrations: list[str]        # "slack", "notion", "github", "caddy", "sqlite", "anthropic"

@dataclass
class Claim:
    sentence: Sentence
    entities: Entities             # post substring-filter
```

**Substring rule (AGENTS.md rule 3):** an entity survives only if
`entity.lower() in sentence.text.lower()` (files also match by basename). Dropped entities
emit an `entity_dropped` event with the original string.

## 3. Verdict side

```python
Status = Literal["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]

@dataclass
class Evidence:
    entity: str
    kind: Literal["file", "symbol", "integration"]
    path: str
    line_no: int | None            # None when matched by path only
    line_text: str | None
    commit_sha: str | None         # commit that introduced the line, when cheaply known; else None

@dataclass
class Verdict:
    sentence_idx: int
    status: Status
    evidence: list[Evidence]       # ≥1 per entity when SUPPORTED
    missing: list[str]             # entities with zero evidence (UNSUPPORTED)
    reason: str                    # one human sentence, shown in UI and Slack
```

## 4. The function — `cdl/grounding/check.py`

```python
def claim_vs_diff(claim: Claim, diff: DiffContext) -> Verdict: ...
def check_staleness(evidence: list[Evidence], diff: DiffContext) -> StaleVerdict: ...
```

### Matching rules (deterministic, in this order)
- **No entities at all** → `UNVERIFIABLE`, reason
  `"Names no file, function, or integration that can be checked against the diff."`
- **file** entity matches if `entity == f.path` or `entity == basename(f.path)` (unique) for any
  `f` in `diff.files` with status ≠ `removed`. Evidence: `kind=file, path, line_no=None`.
- **symbol** entity matches if regex `\b<escaped entity>\b` hits any `added` line in any file.
  Evidence: first hit per file (path, line_no, line_text). Case-sensitive.
- **integration** entity matches if `entity.lower()` appears in any `added` line (lowercased)
  **or** in any changed file's path (lowercased). Aliases in `INTEGRATION_ALIASES`
  (`{"slack": ["slack", "slack_sdk", "xoxb"], "notion": ["notion", "ntn_", "data_source"],
  "github": ["github", "x-hub-signature", "x-github"], "anthropic": ["anthropic", "claude"],
  "sqlite": ["sqlite", "sqlite3"], "caddy": ["caddy", "caddyfile"], "fastapi": ["fastapi",
  "uvicorn"]}`). Evidence: first hit.
- **Verdict:** every entity has ≥1 evidence → `SUPPORTED`; otherwise `UNSUPPORTED` with
  `missing` listing the unmatched entities and reason
  `f"'{missing[0]}' does not appear in the {n} files changed in {base[:7]}..{head[:7]}."`
  If `diff.truncated` and a symbol is missing, append `" (some patches unavailable)"`.

### Staleness rule (same matcher, inverted)
```python
@dataclass
class StaleVerdict:
    stale: bool
    removed: list[Evidence]        # which receipts no longer hold
    reason: str
```
For each `Evidence`:
- `kind=file`: stale if a `FileChange` with `path == ev.path` has status `removed`, or
  `renamed` with `previous_path == ev.path`.
- `kind=symbol`: stale if `\b<symbol>\b` appears in `removed` lines of `ev.path` **and not**
  in `added` lines of any file (net deletion/rename). Body edits that keep the name are not stale.
- `kind=integration`: stale only if every file that matched is `removed`. (Conservative.)
`stale = len(removed) > 0`. Reason: `f"{ev.entity} was removed in {sha[:7]} ({ev.path})"`.

### Why these rules
Line numbers shift on every edit above them — keying on `file + symbol` gives receipts that
survive unrelated changes and flip only when the named thing actually goes away. The rules fit
in one paragraph, which is what lets us print them on the dashboard and hand-label an eval set.

## 5. SQLite schema (`cdl/store.py`)
```sql
pushes(id INTEGER PK, delivery_id TEXT UNIQUE, before_sha, after_sha, ref, received_at, payload_json)
posts(id INTEGER PK, note_page_id TEXT, base_sha, head_sha, status TEXT
      CHECK(status IN ('Blocked','Draft','Sent','Rejected','Stale','Correction','Errored')),
      text TEXT, notion_page_id TEXT, slack_channel TEXT, slack_ts TEXT,
      superseded_by INTEGER REFERENCES posts(id), error TEXT,
      created_at, sent_at, stale_at)
sentences(id INTEGER PK, post_id REFERENCES posts, idx INT, text, source, status, reason, entities_json)
evidence(id INTEGER PK, sentence_id REFERENCES sentences, entity, kind, path, line_no, line_text, commit_sha)
notes_seen(note_page_id TEXT PK, title, status, first_seen_at)
events(id INTEGER PK, ts, kind TEXT, post_id, push_id, data_json)
```
`posts.text` is the joined sentences. Dashboard reads these tables only.

## 6. Events (`events.kind`) — append on every one of these
`push.received`, `push.ignored`, `push.duplicate`, `compare.fetched`, `compare.failed`,
`notes.ready`, `draft.generated`, `entities.extracted`, `entity_dropped`, `verdict`,
`post.blocked`, `post.drafted`, `slack.posted`, `slack.failed`, `notion.written`,
`notion.failed`, `approval.received`, `midcheck.passed`, `midcheck.stale`, `post.sent`,
`post.rejected`, `monitor.checked`, `post.stale`, `correction.threaded`, `llm.call`
(with model, input/output tokens, ms), `error`.
`data_json` carries the relevant ids and the raw reason. Also print each as a JSON line to stdout.

## 7. Notion schema (human creates; agent resolves IDs — `docs/03 §2`)
**Database `Notes`** (input)
| Property | Type | Values |
|---|---|---|
| Name | title | short heading |
| Status | select | `Writing` · `Ready` · `Drafted` |
| Created | created_time | auto |
Body = page content (paragraph blocks). The agent reads blocks, joins paragraphs.

**Database `Posts`** (output mirror)
| Property | Type | Notes |
|---|---|---|
| Name | title | first 60 chars of post or `Correction: …` |
| Status | select | `Blocked` · `Draft` · `Sent` · `Rejected` · `Stale` · `Correction` · `Errored` |
| Post Text | rich_text | ≤ 2000 chars per rich_text object; split if longer |
| Evidence | rich_text | one line per sentence: `✅/⛔/❔ "<sentence>" → path:line (entity), …` |
| Commit Range | rich_text | `abc1234..def5678` |
| Head SHA | rich_text | |
| Slack TS | rich_text | |
| Superseded By | relation → Posts | self-relation |
| Note | relation → Notes | |
| Dashboard | url | `https://commitsdontlie.akashparashar.dev/posts/{id}` |

## 8. LLM I/O shapes (`cdl/llm.py`)
**draft** → JSON `{"sentences": [{"text": str, "source": "notes"|"commits"|"both"}]}`, 3–6
sentences, ≤ 600 chars total, first person, past tense only for things in the commits.
System prompt states the rule verbatim and instructs: *name the concrete file, function, or
integration whenever you can — unverifiable sentences will be rejected.*
**extract_entities** → JSON `{"files": [...], "symbols": [...], "integrations": [...]}` for one
sentence, given the sentence and the list of changed paths (for path spelling only — the
substring rule still applies). Use Anthropic `tool_use` with a JSON schema for both to avoid
prose wrappers.
