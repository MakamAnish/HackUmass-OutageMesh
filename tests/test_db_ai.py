import sqlite3

import db


def _row(msg_id="A1", kw_score=80):
    return {
        "id": msg_id,
        "zone": "CC-1-X",
        "building": "CC",
        "floor": "1",
        "room": None,
        "category": "MED",
        "text": "needs help",
        "level": "HIGH",
        "kw_score": kw_score,
        "kw_reasons": "cat:MED",
        "ai_status": "pending",
    }


def test_init_migrates_ai_fields_without_losing_existing_data(tmp_path):
    path = str(tmp_path / "existing.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE messages (
          id TEXT PRIMARY KEY, zone TEXT NOT NULL, building TEXT, floor TEXT,
          room TEXT, category TEXT NOT NULL, text TEXT NOT NULL, level TEXT NOT NULL,
          kw_score INTEGER, kw_reasons TEXT, ai_level TEXT, ai_reason TEXT,
          rssi INTEGER, snr REAL, received_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'new', synced INTEGER NOT NULL DEFAULT 0,
          sos INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO messages (id, zone, category, text, level, received_at)
        VALUES ('A1', 'CC-1-X', 'MED', 'old request', 'HIGH', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()

    db.init(path)
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    saved = conn.execute("SELECT text, level FROM messages WHERE id='A1'").fetchone()
    conn.close()

    assert {"ai_urgency", "ai_reason", "ai_summary", "ai_people_affected", "ai_status"} <= columns
    assert saved == ("old request", "HIGH")
    assert db.list_messages(db_path=path)[0]["ai_status"] == "offline"


def test_ai_result_updates_record_and_cannot_lower_keyword_floor(tmp_path):
    path = str(tmp_path / "messages.db")
    db.init(path)
    db.insert(_row(kw_score=80), db_path=path)

    updated = db.update_ai_result(
        "A1",
        {
            "urgency": 1,
            "category": "other",
            "people_affected": None,
            "reason": "The details are not enough to reduce the keyword priority.",
            "summary": "Medical request",
        },
        db_path=path,
    )

    record = db.list_messages(db_path=path)[0]
    assert updated is True
    assert record["ai_urgency"] == 1
    assert record["ai_status"] == "done"
    assert record["level"] == "HIGH"
    assert record["ai_summary"] == "Medical request"
    assert record["ai_people_affected"] is None


def test_duplicate_insert_and_ai_update_are_ignored(tmp_path):
    path = str(tmp_path / "messages.db")
    db.init(path)
    assert db.insert(_row(), db_path=path) is not None
    assert db.insert(_row(), db_path=path) is None
    assert db.mark_ai_offline("A1", db_path=path) is True
    assert db.update_ai_result(
        "A1",
        {
            "urgency": 5,
            "category": "medical",
            "people_affected": 1,
            "reason": "Urgent.",
            "summary": "Urgent",
        },
        db_path=path,
    ) is False
    assert db.list_messages(db_path=path)[0]["ai_status"] == "offline"
