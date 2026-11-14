"""Background radio thread: listen -> on_packet; shared send() for ACKs."""
import logging
import threading
import time

from radio import BaseRadio, WioE5, get_radio

log = logging.getLogger("radio_worker")
_radio: BaseRadio | None = None
_lock = threading.Lock()


def get() -> BaseRadio:
    global _radio
    with _lock:
        if _radio is None:
            _radio = get_radio()
        return _radio


def send(payload: str) -> bool:
    """Transmit, then re-arm RX so the listen loop keeps working."""
    r = get()
    ok = r.send(payload)
    if isinstance(r, WioE5):
        try:
            r.arm_rx()
        except Exception as e:
            log.warning("re-arm RX after send failed: %s", e)
    return ok


def _run(on_packet) -> None:
    global _radio
    while True:
        try:
            get().listen(on_packet)
        except Exception as e:
            log.warning("radio worker error: %s; retrying in 2s", e)
            with _lock:
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
