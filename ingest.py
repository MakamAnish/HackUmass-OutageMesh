import logging
from datetime import datetime, timezone

from db import insert_outcome
from protocol import decode, decode_ack, decode_done
from triage import final_level, keyword_score

logger = logging.getLogger(__name__)


def ingest(
    payload: str,
    rssi: int | None = None,
    snr: float | None = None,
    *,
    outcome: dict | None = None,
) -> dict | None:
    """Decode a packet, triage it, and store a row. Returns the stored row or None.

    When outcome is given, outcome["created"] is True only if this call inserted
    a new row. Retransmissions of an existing id return the stored row with
    created False. Callers that ignore outcome keep the previous return value.
    """

    def finish(row, created: bool):
        if outcome is not None:
            outcome["created"] = created
        return row

    if decode_ack(payload) is not None or decode_done(payload) is not None:
        # Status packets are for the help point (sender), not the dashboard.
        return finish(None, False)
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
            "rssi": rssi,
            "snr": snr,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "status": "new",
            "synced": 0,
            "sos": 1 if decoded.get("sos") else 0,
            "range_m": decoded.get("range_m"),
        }
        stored, created = insert_outcome(row)
        return finish(stored, created)
    except Exception:  # pragma: no cover - defensive per contract
        logger.exception("Dropping invalid packet: %s", payload)
        return finish(None, False)
