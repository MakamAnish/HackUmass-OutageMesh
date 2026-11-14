import logging
from datetime import datetime, timezone

from db import insert
from protocol import decode, decode_ack
from triage import final_level, keyword_score

logger = logging.getLogger(__name__)


def ingest(payload: str, rssi: int | None = None, snr: float | None = None) -> dict | None:
    """Decode a packet, triage it, and store a row. Returns the stored row or None."""
    if decode_ack(payload) is not None:
        # ACKs are for the help point (sender), not the dashboard.
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
            "rssi": rssi,
            "snr": snr,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "status": "new",
            "synced": 0,
        }
        return insert(row)
    except Exception as exc:  # pragma: no cover - defensive per contract
        logger.exception("Dropping invalid packet: %s", payload)
        return None
