"""Background thread on the receiver: radio.listen -> on_packet (P2's ingest)."""
import logging
import threading
import time

from radio import BaseRadio, get_radio

log = logging.getLogger("radio_worker")
_radio: BaseRadio | None = None


def _run(on_packet) -> None:
    global _radio
    while True:
        try:
            if _radio is None:
                _radio = get_radio()
            _radio.listen(on_packet)
        except Exception as e:
            log.warning("radio worker error: %s; retrying in 2s", e)
            _radio = None
            time.sleep(2)


def start(on_packet) -> threading.Thread:
    t = threading.Thread(target=_run, args=(on_packet,), name="radio_worker", daemon=True)
    t.start()
    return t


def health() -> dict:
    if _radio is None:
        return {"ok": False, "mode": None, "last_rx": None, "error": "radio not connected"}
    return _radio.health()
