"""Laptop A: persist help requests this node sent, plus ACK / reply status."""
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
          acked_at TEXT,
          reply_text TEXT
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
) -> dict:
    init(db_path)
    row = {
        "id": msg_id,
        "client_id": client_id or "",
        "zone": zone,
        "category": category,
        "text": text,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "acked_at": None,
        "reply_text": None,
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO outgoing
              (id, client_id, zone, category, text, sent_at, acked_at, reply_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["client_id"],
                row["zone"],
                row["category"],
                row["text"],
                row["sent_at"],
                None,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return row


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
