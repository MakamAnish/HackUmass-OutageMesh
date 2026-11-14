"""Background radio thread: listen -> on_packet; shared send() for ACKs."""
import logging
import threading
import time

from radio import BaseRadio, get_radio

log = logging.getLogger("radio_worker")
_radio: BaseRadio | None = None
_lock = threading.Lock()


def get() -> BaseRadio:
    global _radio
    with _lock:
        if _radio is None:
            _radio = get_radio()
        return _radio


def reset() -> None:
    """Drop the cached radio so the next get()/send() reopens the port."""
    global _radio
    with _lock:
        if _radio is not None:
            try:
                _radio.close()
            except Exception:
                pass
            _radio = None


def send(payload: str) -> bool:
    """Transmit (WioE5.send re-arms RX under the same lock)."""
    try:
        return get().send(payload)
    except Exception as e:
        log.warning("send error: %s; resetting radio", e)
        reset()
        try:
            return get().send(payload)
        except Exception as e2:
            log.warning("send retry failed: %s", e2)
            return False


def _run(on_packet) -> None:
    global _radio
    while True:
        try:
            get().listen(on_packet)
        except Exception as e:
            log.warning("radio worker error: %s; retrying in 2s", e)
            reset()
            time.sleep(2)


def start(on_packet) -> threading.Thread:
    t = threading.Thread(target=_run, args=(on_packet,), name="radio_worker", daemon=True)
    t.start()
    return t


def health() -> dict:
    if _radio is None:
        return {"ok": False, "mode": None, "last_rx": None, "error": "radio not connected"}
    return _radio.health()
