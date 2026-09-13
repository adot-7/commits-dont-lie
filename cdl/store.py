"""SQLite persistence and the append-only CDL event log.

This module owns schema creation, CRUD helpers, and JSON-line event logging.
It must not know about GitHub, Notion, Slack, or LLM protocols; callers pass
plain values and contracts instead.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Claim, DiffContext, Evidence, Note, PushRecord, Sentence, Verdict, dataclass_dict


SCHEMA = """
CREATE TABLE IF NOT EXISTS pushes(
    id INTEGER PRIMARY KEY,
    delivery_id TEXT UNIQUE NOT NULL,
    before_sha TEXT NOT NULL,
    after_sha TEXT NOT NULL,
    ref TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts(
    id INTEGER PRIMARY KEY,
    note_page_id TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Blocked','Draft','Sent','Rejected','Stale','Correction','Errored')),
    text TEXT NOT NULL DEFAULT '',
    notion_page_id TEXT,
    slack_channel TEXT,
    slack_ts TEXT,
    superseded_by INTEGER REFERENCES posts(id),
    error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    stale_at TEXT
);
CREATE TABLE IF NOT EXISTS sentences(
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    entities_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(post_id, idx)
);
CREATE TABLE IF NOT EXISTS evidence(
    id INTEGER PRIMARY KEY,
    sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    entity TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    line_no INTEGER,
    line_text TEXT,
    commit_sha TEXT
);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    post_id INTEGER,
    push_id INTEGER,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes_seen(
    note_page_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_actions(
    action_ts TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
"""


def _now() -> str:
    """Return an ISO-8601 UTC timestamp for persisted records."""

    return datetime.now(timezone.utc).isoformat()


def _path(path: str | Path | None = None) -> Path:
    """Resolve a database path without creating a connection."""

    return Path(path or "data/cdl.sqlite")


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a configured SQLite connection with rows and foreign keys enabled."""

    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Create the CDL schema and return an initialized connection."""

    connection = connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def _json(value: Any) -> str:
    """Encode event and contract data consistently for SQLite."""

    return json.dumps(dataclass_dict(value), ensure_ascii=False, sort_keys=True)


class Store:
    """Small SQLite repository used by request and background tasks."""

    def __init__(self, path: str | Path | None = None):
        self.path = _path(path)
        connection = init_db(self.path)
        connection.close()

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived connection for one operation."""

        return connect(self.path)

    def append_event(
        self,
        kind: str,
        data: Any | None = None,
        *,
        post_id: int | None = None,
        push_id: int | None = None,
    ) -> dict[str, Any]:
        """Append and print one structured event, returning its JSON object."""

        event = {
            "ts": _now(),
            "kind": kind,
            "post_id": post_id,
            "push_id": push_id,
            "data": data if data is not None else {},
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        print(line, file=sys.stdout, flush=True)
        connection = self._connect()
        try:
            cursor = connection.execute(
                "INSERT INTO events(ts, kind, post_id, push_id, data_json) VALUES(?,?,?,?,?)",
                (event["ts"], kind, post_id, push_id, _json(event["data"])),
            )
            connection.commit()
            event["id"] = cursor.lastrowid
        finally:
            connection.close()
        return event

    def insert_push(
        self,
        delivery_id: str,
        before_sha: str,
        after_sha: str,
        ref: str,
        payload: dict[str, Any] | str,
        *,
        received_at: str | None = None,
    ) -> int | None:
        """Insert a webhook delivery, returning its id or ``None`` on duplicate."""

        payload_json = payload if isinstance(payload, str) else _json(payload)
        connection = self._connect()
        try:
            try:
                cursor = connection.execute(
                    """INSERT INTO pushes(delivery_id,before_sha,after_sha,ref,received_at,payload_json)
                       VALUES(?,?,?,?,?,?)""",
                    (delivery_id, before_sha, after_sha, ref, received_at or _now(), payload_json),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return None
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def get_push(self, push_id: int) -> PushRecord | None:
        """Load a persisted push by numeric id."""

        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM pushes WHERE id=?", (push_id,)).fetchone()
            if row is None:
                return None
            return PushRecord(**dict(row))
        finally:
            connection.close()

    def list_pushes(self) -> list[dict[str, Any]]:
        """Return persisted pushes newest-first for diagnostics."""

        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute("SELECT * FROM pushes ORDER BY id DESC")]
        finally:
            connection.close()

    def create_post(
        self,
        note_page_id: str,
        base_sha: str,
        head_sha: str,
        status: str,
        text: str,
        *,
        error: str | None = None,
        notion_page_id: str | None = None,
        slack_channel: str | None = None,
    ) -> int:
        """Create a post row and return its local id."""

        connection = self._connect()
        try:
            cursor = connection.execute(
                """INSERT INTO posts(note_page_id,base_sha,head_sha,status,text,error,notion_page_id,slack_channel,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (note_page_id, base_sha, head_sha, status, text, error, notion_page_id, slack_channel, _now()),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def post_exists(self, note_page_id: str, head_sha: str) -> bool:
        """Check the post idempotency key ``(note_page_id, head_sha)``."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM posts WHERE note_page_id=? AND head_sha=? LIMIT 1",
                (note_page_id, head_sha),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def mark_note_seen(self, note_page_id: str, title: str, status: str = "Ready") -> None:
        """Remember the latest metadata observed for one Notion note."""

        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO notes_seen(note_page_id,title,status,first_seen_at) VALUES(?,?,?,?)
                   ON CONFLICT(note_page_id) DO UPDATE SET title=excluded.title,status=excluded.status""",
                (note_page_id, title, status, _now()),
            )
            connection.commit()
        finally:
            connection.close()

    def add_sentences(self, post_id: int, claims: Iterable[Claim], verdicts: Iterable[Verdict]) -> None:
        """Persist sentence, entity, verdict, and evidence rows atomically."""

        claim_list = list(claims)
        verdict_list = list(verdicts)
        if len(claim_list) != len(verdict_list):
            raise ValueError("claims and verdicts must have the same length")
        connection = self._connect()
        try:
            for claim, verdict in zip(claim_list, verdict_list):
                cursor = connection.execute(
                    """INSERT INTO sentences(post_id,idx,text,source,status,reason,entities_json)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        post_id,
                        claim.sentence.idx,
                        claim.sentence.text,
                        claim.sentence.source,
                        verdict.status,
                        verdict.reason,
                        _json(claim.entities),
                    ),
                )
                sentence_id = int(cursor.lastrowid)
                connection.executemany(
                    """INSERT INTO evidence(sentence_id,entity,kind,path,line_no,line_text,commit_sha)
                       VALUES(?,?,?,?,?,?,?)""",
                    [
                        (sentence_id, ev.entity, ev.kind, ev.path, ev.line_no, ev.line_text, ev.commit_sha)
                        for ev in verdict.evidence
                    ],
                )
            connection.commit()
        finally:
            connection.close()

    def get_post(self, post_id: int) -> dict[str, Any] | None:
        """Return one post with decoded sentence and evidence children."""

        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            if row is None:
                return None
            post = dict(row)
            sentence_rows = connection.execute(
                "SELECT * FROM sentences WHERE post_id=? ORDER BY idx", (post_id,)
            ).fetchall()
            post["sentences"] = []
            for sentence_row in sentence_rows:
                sentence = dict(sentence_row)
                sentence["entities"] = json.loads(sentence.pop("entities_json"))
                sentence["evidence"] = [
                    dict(ev)
                    for ev in connection.execute(
                        "SELECT * FROM evidence WHERE sentence_id=? ORDER BY id", (sentence["id"],)
                    ).fetchall()
                ]
                post["sentences"].append(sentence)
            return post
        finally:
            connection.close()

    def get_superseding_post(self, correction_id: int) -> dict[str, Any] | None:
        """Return the original post linked to a correction row, if present."""

        connection = self._connect()
        try:
            row = connection.execute("SELECT id FROM posts WHERE superseded_by=? LIMIT 1", (correction_id,)).fetchone()
        finally:
            connection.close()
        return self.get_post(int(row["id"])) if row else None

    def list_posts(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """Return dashboard post summaries newest-first."""

        connection = self._connect()
        try:
            if status:
                rows = connection.execute(
                    "SELECT * FROM posts WHERE status=? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM posts ORDER BY id DESC").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                first = connection.execute(
                    "SELECT text FROM sentences WHERE post_id=? ORDER BY idx LIMIT 1", (item["id"],)
                ).fetchone()
                item["first_sentence"] = first["text"] if first else item["text"].splitlines()[0] if item["text"] else ""
                result.append(item)
            return result
        finally:
            connection.close()

    def list_sent_posts(self) -> list[dict[str, Any]]:
        """Return Sent posts with all stored evidence for monitoring."""

        return [post for post in (self.get_post(row["id"]) for row in self.list_posts(status="Sent")) if post]

    def update_post(self, post_id: int, **fields: Any) -> None:
        """Update allow-listed post fields and timestamps."""

        allowed = {
            "status",
            "text",
            "notion_page_id",
            "slack_channel",
            "slack_ts",
            "superseded_by",
            "error",
            "sent_at",
            "stale_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown post fields: {', '.join(sorted(unknown))}")
        if not fields:
            return
        assignments = ", ".join(f"{field}=?" for field in fields)
        connection = self._connect()
        try:
            connection.execute(
                f"UPDATE posts SET {assignments} WHERE id=?",
                [*fields.values(), post_id],
            )
            connection.commit()
        finally:
            connection.close()

    def last_posted_sha(self) -> str | None:
        """Return the newest Sent post head SHA, if one exists."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT head_sha FROM posts WHERE status='Sent' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["head_sha"] if row else None
        finally:
            connection.close()

    def mark_action(self, action_ts: str) -> bool:
        """Atomically remember a Slack action; return false for duplicates."""

        connection = self._connect()
        try:
            try:
                connection.execute(
                    "INSERT INTO processed_actions(action_ts,received_at) VALUES(?,?)",
                    (action_ts, _now()),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            connection.commit()
            return True
        finally:
            connection.close()

    def counts(self) -> dict[str, Any]:
        """Return production counters used by the dashboard and brief."""

        connection = self._connect()
        try:
            counts: dict[str, Any] = {}
            counts["pushes"] = connection.execute("SELECT COUNT(*) AS n FROM pushes").fetchone()["n"]
            counts["posts_by_status"] = {
                row["status"]: row["n"]
                for row in connection.execute("SELECT status, COUNT(*) AS n FROM posts GROUP BY status")
            }
            counts["sentences_by_verdict"] = {
                row["status"]: row["n"]
                for row in connection.execute("SELECT status, COUNT(*) AS n FROM sentences GROUP BY status")
            }
            counts["entity_dropped"] = connection.execute(
                "SELECT COUNT(*) AS n FROM events WHERE kind='entity_dropped'"
            ).fetchone()["n"]
            llm_rows = connection.execute("SELECT data_json FROM events WHERE kind='llm.call'").fetchall()
            counts["llm_input_tokens"] = 0
            counts["llm_output_tokens"] = 0
            counts["llm_calls"] = len(llm_rows)
            for row in llm_rows:
                data = json.loads(row["data_json"])
                counts["llm_input_tokens"] += int(data.get("input_tokens", 0) or 0)
                counts["llm_output_tokens"] += int(data.get("output_tokens", 0) or 0)
            total_tokens = counts["llm_input_tokens"] + counts["llm_output_tokens"]
            counts["llm_estimated_cost"] = round(total_tokens / 1_000_000 * 5.0, 4)
            return counts
        finally:
            connection.close()


def append_event(
    kind: str,
    data: Any | None = None,
    *,
    post_id: int | None = None,
    push_id: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Module-level convenience wrapper for the required event API."""

    return Store(db_path).append_event(kind, data, post_id=post_id, push_id=push_id)
