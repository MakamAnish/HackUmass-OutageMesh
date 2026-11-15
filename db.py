import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "outage.db")


def init(db_path: str = DB_PATH) -> str:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY,
          zone TEXT NOT NULL,
          building TEXT,
          floor TEXT,
          room TEXT,
          category TEXT NOT NULL,
          text TEXT NOT NULL,
          level TEXT NOT NULL,
          kw_score INTEGER,
          kw_reasons TEXT,
          ai_level TEXT,
          ai_reason TEXT,
          ai_urgency INTEGER,
          ai_summary TEXT,
          ai_people_affected INTEGER,
          ai_status TEXT NOT NULL DEFAULT 'offline',
          rssi INTEGER,
          snr REAL,
          received_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'new',
          synced INTEGER NOT NULL DEFAULT 0,
          sos INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    migrations = {
        "ai_reason": "TEXT",
        "ai_urgency": "INTEGER",
        "ai_summary": "TEXT",
        "ai_people_affected": "INTEGER",
        "ai_status": "TEXT NOT NULL DEFAULT 'offline'",
        "sos": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in migrations.items():
        if column not in cols:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
    conn.commit()
    conn.close()
    return db_path


def _connect(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_db(db_path: str = DB_PATH):
    init(db_path)


def insert(row: dict[str, Any], db_path: str = DB_PATH) -> dict | None:
    _ensure_db(db_path)
    prepared = {
        "id": row.get("id"),
        "zone": row.get("zone"),
        "building": row.get("building"),
        "floor": row.get("floor"),
        "room": row.get("room"),
        "category": row.get("category"),
        "text": row.get("text"),
        "level": row.get("level") or "LOW",
        "kw_score": row.get("kw_score"),
        "kw_reasons": row.get("kw_reasons"),
        "ai_level": row.get("ai_level"),
        "ai_reason": row.get("ai_reason"),
        "ai_urgency": row.get("ai_urgency"),
        "ai_summary": row.get("ai_summary"),
        "ai_people_affected": row.get("ai_people_affected"),
        "ai_status": row.get("ai_status") or "offline",
        "rssi": row.get("rssi"),
        "snr": row.get("snr"),
        "received_at": row.get("received_at") or datetime.now(timezone.utc).isoformat(),
        "status": row.get("status") or "new",
        "synced": int(row.get("synced") or 0),
        "sos": 1 if row.get("sos") or row.get("category") == "SOS" else 0,
    }
    if not prepared["id"]:
        return None
    conn = _connect(db_path)
    try:
        inserted = conn.execute(
            """
            INSERT OR IGNORE INTO messages (
              id, zone, building, floor, room, category, text, level, kw_score,
              kw_reasons, ai_level, ai_reason, ai_urgency, ai_summary,
              ai_people_affected, ai_status, rssi, snr, received_at, status, synced,
              sos
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prepared["id"],
                prepared["zone"],
                prepared["building"],
                prepared["floor"],
                prepared["room"],
                prepared["category"],
                prepared["text"],
                prepared["level"],
                prepared["kw_score"],
                prepared["kw_reasons"],
                prepared["ai_level"],
                prepared["ai_reason"],
                prepared["ai_urgency"],
                prepared["ai_summary"],
                prepared["ai_people_affected"],
                prepared["ai_status"],
                prepared["rssi"],
                prepared["snr"],
                prepared["received_at"],
                prepared["status"],
                prepared["synced"],
                prepared["sos"],
            ),
        )
        conn.commit()
        if inserted.rowcount == 0:
            return None
        cur = conn.execute("SELECT * FROM messages WHERE id = ?", (prepared["id"],))
        stored = dict(cur.fetchone() or {})
    finally:
        conn.close()
    return stored


def update_ai_result(msg_id: str, result: dict, db_path: str = DB_PATH) -> bool:
    """Store one validated AI result and apply it above the keyword safety floor."""
    from triage import final_level

    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT kw_score FROM messages WHERE id = ? AND ai_status = 'pending'",
            (msg_id,),
        ).fetchone()
        if row is None:
            return False
        urgency = result["urgency"]
        keyword = {"score": row["kw_score"]}
        level = final_level(keyword, result)
        updated = conn.execute(
            """
            UPDATE messages
            SET ai_urgency = ?, ai_reason = ?, ai_summary = ?,
                ai_people_affected = ?, ai_status = 'done', level = ?
            WHERE id = ? AND ai_status = 'pending'
            """,
            (
                urgency,
                result["reason"],
                result["summary"],
                result["people_affected"],
                level,
                msg_id,
            ),
        )
        conn.commit()
        return updated.rowcount > 0
    finally:
        conn.close()


def mark_ai_offline(msg_id: str, db_path: str = DB_PATH) -> bool:
    conn = _connect(db_path)
    try:
        updated = conn.execute(
            "UPDATE messages SET ai_status = 'offline' WHERE id = ? AND ai_status = 'pending'",
            (msg_id,),
        )
        conn.commit()
        return updated.rowcount > 0
    finally:
        conn.close()


def mark_pending_ai_offline(db_path: str = DB_PATH) -> int:
    """Mark work interrupted by a prior process exit as unavailable, without retrying."""
    conn = _connect(db_path)
    try:
        updated = conn.execute(
            "UPDATE messages SET ai_status = 'offline' WHERE ai_status = 'pending'"
        )
        conn.commit()
        return updated.rowcount
    finally:
        conn.close()


def list_messages(since: str | None = None, db_path: str = DB_PATH):
    _ensure_db(db_path)
    conn = _connect(db_path)
    try:
        query = "SELECT * FROM messages"
        params = []
        if since:
            query += " WHERE received_at >= ?"
            params.append(since)
        # Open items first, then urgency (HIGH > MEDIUM > LOW), then newest.
        query += """
          ORDER BY
            CASE status WHEN 'resolved' THEN 1 ELSE 0 END,
            CASE WHEN sos = 1 OR category = 'SOS' THEN 0 ELSE 1 END,
            CASE level WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END,
            received_at DESC
        """
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_status(msg_id: str, status: str, db_path: str = DB_PATH) -> bool:
    _ensure_db(db_path)
    conn = _connect(db_path)
    try:
        updated = conn.execute(
            "UPDATE messages SET status = ? WHERE id = ?",
            (status, msg_id),
        )
        conn.commit()
        return updated.rowcount > 0
    finally:
        conn.close()
