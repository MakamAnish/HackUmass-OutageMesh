"""Laptop A: persist help requests this node sent, plus ACK / resolve status."""
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "outgoing.db")


def init(db_path: str = DB_PATH) -> str:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outgoing (
          id TEXT PRIMARY KEY,
          client_id TEXT,
          zone TEXT NOT NULL,
          category TEXT NOT NULL,
          text TEXT NOT NULL,
          sent_at TEXT NOT NULL,
          send_state TEXT NOT NULL DEFAULT 'sent',
          packet TEXT,
          send_attempts INTEGER NOT NULL DEFAULT 0,
          acked_at TEXT,
          reply_text TEXT,
          resolved_at TEXT,
          resolve_text TEXT
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(outgoing)")}
    if "resolved_at" not in cols:
        conn.execute("ALTER TABLE outgoing ADD COLUMN resolved_at TEXT")
    if "resolve_text" not in cols:
        conn.execute("ALTER TABLE outgoing ADD COLUMN resolve_text TEXT")
    for column, definition in {
        "send_state": "TEXT NOT NULL DEFAULT 'sent'",
        "packet": "TEXT",
        "send_attempts": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if column not in cols:
            conn.execute(f"ALTER TABLE outgoing ADD COLUMN {column} {definition}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS acks (
          id TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('HEARD', 'ON_WAY', 'DONE')),
          note TEXT NOT NULL DEFAULT '',
          ts TEXT NOT NULL,
          PRIMARY KEY (id, status)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outgoing_client ON outgoing(client_id)")
    conn.commit()
    conn.close()
    return db_path


def _connect(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def record(
    msg_id: str,
    zone: str,
    category: str,
    text: str,
    client_id: str | None = None,
    db_path: str = DB_PATH,
    packet: str | None = None,
) -> dict:
    init(db_path)
    row = {
        "id": msg_id,
        "client_id": client_id or "",
        "zone": zone,
        "category": category,
        "text": text,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "send_state": "sending",
        "packet": packet,
        "send_attempts": 0,
        "acked_at": None,
        "reply_text": None,
        "resolved_at": None,
        "resolve_text": None,
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO outgoing
              (id, client_id, zone, category, text, sent_at, send_state, packet, send_attempts,
               acked_at, reply_text, resolved_at, resolve_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["client_id"],
                row["zone"],
                row["category"],
                row["text"],
                row["sent_at"],
                row["send_state"],
                row["packet"],
                row["send_attempts"],
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return row


def set_send_state(
    msg_id: str,
    state: str,
    attempts: int,
    db_path: str = DB_PATH,
) -> bool:
    init(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE outgoing SET send_state = ?, send_attempts = ? WHERE id = ?",
            (state, attempts, msg_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_ack(
    msg_id: str,
    status: str,
    note: str = "",
    db_path: str = DB_PATH,
) -> bool:
    if status not in {"HEARD", "ON_WAY", "DONE"}:
        return False
    init(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO acks (id, status, note, ts) VALUES (?, ?, ?, ?)",
            (msg_id, status, note, now),
        )
        if cur.rowcount == 0:
            conn.commit()
            return False
        if status == "HEARD":
            conn.execute(
                "UPDATE outgoing SET acked_at = ?, reply_text = COALESCE(?, reply_text) WHERE id = ?",
                (now, note or None, msg_id),
            )
        elif status == "ON_WAY":
            conn.execute(
                "UPDATE outgoing SET acked_at = COALESCE(acked_at, ?), reply_text = COALESCE(?, reply_text) WHERE id = ?",
                (now, note or None, msg_id),
            )
        elif status == "DONE":
            conn.execute(
                """
                UPDATE outgoing
                SET resolved_at = ?, resolve_text = COALESCE(?, resolve_text),
                    acked_at = COALESCE(acked_at, ?)
                WHERE id = ?
                """,
                (now, note or None, now, msg_id),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def get_status(msg_id: str, db_path: str = DB_PATH) -> dict | None:
    init(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, sent_at, send_state, send_attempts FROM outgoing WHERE id = ?",
            (msg_id,),
        ).fetchone()
        if row is None:
            return None
        ack = conn.execute(
            """
            SELECT status, note, ts FROM acks
            WHERE id = ?
            ORDER BY CASE status WHEN 'DONE' THEN 3 WHEN 'ON_WAY' THEN 2 ELSE 1 END DESC
            LIMIT 1
            """,
            (msg_id,),
        ).fetchone()
        noted_ack = conn.execute(
            """
            SELECT note FROM acks
            WHERE id = ? AND note != ''
            ORDER BY CASE status WHEN 'DONE' THEN 3 WHEN 'ON_WAY' THEN 2 ELSE 1 END DESC,
                     ts DESC
            LIMIT 1
            """,
            (msg_id,),
        ).fetchone()
        state = ack["status"] if ack else (
            "SENT" if row["send_state"] == "sent" else
            "FAILED" if row["send_state"] == "failed" else "SENDING"
        )
        return {
            "id": row["id"],
            "state": state,
            "note": noted_ack["note"] if noted_ack else "",
            "updated": ack["ts"] if ack else row["sent_at"],
            "send_state": row["send_state"],
            "send_attempts": row["send_attempts"],
        }
    finally:
        conn.close()


def mark_acked(msg_id: str, reply_text: str | None = None, db_path: str = DB_PATH) -> bool:
    init(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE outgoing
            SET acked_at = ?, reply_text = COALESCE(?, reply_text)
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), reply_text or None, msg_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_resolved(msg_id: str, resolve_text: str | None = None, db_path: str = DB_PATH) -> bool:
    init(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE outgoing
            SET resolved_at = ?,
                resolve_text = COALESCE(?, resolve_text),
                acked_at = COALESCE(acked_at, ?)
            WHERE id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                resolve_text or None,
                datetime.now(timezone.utc).isoformat(),
                msg_id,
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_one(msg_id: str, db_path: str = DB_PATH) -> dict | None:
    init(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM outgoing WHERE id = ?", (msg_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_for_client(client_id: str, limit: int = 50, db_path: str = DB_PATH) -> list[dict[str, Any]]:
    init(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM outgoing
            WHERE client_id = ?
            ORDER BY sent_at DESC
            LIMIT ?
            """,
            (client_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_by_ids(ids: list[str], db_path: str = DB_PATH) -> list[dict[str, Any]]:
    init(db_path)
    if not ids:
        return []
    conn = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM outgoing WHERE id IN ({placeholders}) ORDER BY sent_at DESC",
            ids,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
