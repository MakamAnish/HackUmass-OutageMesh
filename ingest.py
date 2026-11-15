import logging
import threading
from datetime import datetime, timezone

from ai_triage import analyze_message
from db import insert, mark_ai_offline, update_ai_result
from protocol import decode, decode_ack, decode_done
from triage import final_level, keyword_score

logger = logging.getLogger(__name__)


def _run_ai_triage(row: dict) -> None:
    try:
        result = analyze_message(
            row["category"],
            row["text"],
            zone=row["zone"],
            gps=row.get("gps"),
        )
        if result is None:
            mark_ai_offline(row["id"])
        else:
            update_ai_result(row["id"], result)
    except Exception:
        logger.exception("AI triage update failed for message %s", row.get("id"))
        try:
            mark_ai_offline(row["id"])
        except Exception:
            logger.exception("Could not mark AI triage offline for message %s", row.get("id"))


def _start_ai_triage(row: dict) -> None:
    try:
        threading.Thread(
            target=_run_ai_triage,
            args=(row,),
            name=f"ai-triage-{row['id']}",
            daemon=True,
        ).start()
    except Exception:
        logger.exception("Could not start AI triage for message %s", row.get("id"))
        try:
            mark_ai_offline(row["id"])
        except Exception:
            logger.exception("Could not mark AI triage offline for message %s", row.get("id"))


def ingest(payload: str, rssi: int | None = None, snr: float | None = None) -> dict | None:
    """Decode a packet, triage it, and store a row. Returns the stored row or None."""
    if decode_ack(payload) is not None or decode_done(payload) is not None:
        # Status packets are for the help point (sender), not the dashboard.
        return None
    try:
        decoded = decode(payload)
        kw = keyword_score(decoded["category"], decoded["text"])
        level = final_level(kw, None)
        row = {
            "id": decoded["id"],
            "zone": decoded["zone"],
            "building": decoded["building"],
            "floor": decoded["floor"],
            "room": decoded["room"],
            "category": decoded["category"],
            "text": decoded["text"],
            "level": level,
            "kw_score": kw["score"],
            "kw_reasons": "|".join(kw["reasons"]),
            "ai_level": None,
            "ai_reason": None,
            "ai_urgency": None,
            "ai_summary": None,
            "ai_people_affected": None,
            "ai_status": "pending",
            "rssi": rssi,
            "snr": snr,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "status": "new",
            "synced": 0,
            "sos": 1 if decoded.get("sos") else 0,
        }
        stored = insert(row)
        if stored is not None:
            _start_ai_triage({**stored, "gps": decoded.get("gps")})
        return stored
    except Exception as exc:  # pragma: no cover - defensive per contract
        logger.exception("Dropping invalid packet: %s", payload)
        return None
