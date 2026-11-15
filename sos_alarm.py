"""Receiver-side audible SOS alarm.

One shared controller plays a local WAV through the laptop speakers until a
responder silences it. Playback is asynchronous and independent of message
acknowledgement. A later, different SOS can start it again.
"""
import atexit
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("sos_alarm")

WAV_PATH = Path(__file__).resolve().parent / "static" / "audio" / "sos_alarm.wav"


class SOSAlarm:
    def __init__(self, player=None, wav_path: Path | None = None):
        self._lock = threading.Lock()
        self._active = False
        self._error: str | None = None
        self._wav_path = Path(wav_path) if wav_path is not None else WAV_PATH
        self._player = player if player is not None else default_player()
        if hasattr(self._player, "on_failure"):
            self._player.on_failure = self._mark_failed

    def start(self) -> None:
        """Start the repeating alarm if it is not already active."""
        with self._lock:
            if self._active:
                return
            self._active = True
            self._error = None
        try:
            if not self._wav_path.is_file():
                raise RuntimeError(f"alarm sound missing: {self._wav_path}")
            self._player.play(str(self._wav_path))
        except Exception as exc:
            logger.exception("SOS alarm playback failed")
            with self._lock:
                self._active = False
                self._error = str(exc)
            try:
                self._player.stop()
            except Exception:
                logger.exception("SOS alarm stop after playback failure failed")
            return
        with self._lock:
            silenced = not self._active
        if silenced:
            self._stop_player()
            return
        logger.info("SOS alarm started")

    def stop(self) -> None:
        """Stop the alarm. Repeated calls are harmless."""
        with self._lock:
            was_active = self._active
            self._active = False
        stopped = self._stop_player()
        with self._lock:
            if stopped:
                self._error = None
            elif was_active:
                self._active = True
        if was_active and stopped:
            logger.info("SOS alarm silenced")

    def is_active(self) -> bool:
        """Return whether the alarm is currently active."""
        with self._lock:
            return self._active

    def status(self) -> dict:
        with self._lock:
            body = {"active": self._active}
            if self._error:
                body["error"] = self._error
            return body

    def _mark_failed(self, exc: BaseException) -> None:
        logger.error("SOS alarm playback failed: %s", exc)
        with self._lock:
            self._active = False
            self._error = str(exc)

    def _stop_player(self) -> bool:
        try:
            self._player.stop()
            return True
        except Exception as exc:
            logger.exception("SOS alarm stop failed")
            with self._lock:
                self._error = str(exc)
            return False


class MciLoopPlayer:
    """Loop a WAV with the Windows MCI API. stop() interrupts playback."""

    _alias = "tether_sos_alarm"

    def __init__(self):
        self.on_failure = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._open = False
        self._send = None
        self._error_text = None

    def play(self, path: str) -> None:
        self._ensure_api()
        with self._lock:
            if self._thread and self._thread.is_alive() and not self._stop.is_set():
                return
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        with self._lock:
            if self._thread and self._thread.is_alive() and not self._stop.is_set():
                return
            self._stop.clear()
            self._open_locked(path)
            code, detail = self._mci_locked(f"play {self._alias}")
            if code != 0:
                self._close_locked()
                raise RuntimeError(detail or f"audio play failed ({code})")
            self._thread = threading.Thread(target=self._loop, name="sos_alarm", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._close_locked()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=0.5)

    def _loop(self) -> None:
        # play() already started the first pass. Restart only after it ends.
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                if self._stop.wait(0.05):
                    return
                with self._lock:
                    if self._stop.is_set() or not self._open:
                        return
                    mode = self._status_locked()
                if mode == "playing":
                    continue
                # Ignore a stale "stopped" reading while the device is starting.
                if time.monotonic() - started < 0.2:
                    continue
                with self._lock:
                    if self._stop.is_set() or not self._open:
                        return
                    self._mci_locked(f"seek {self._alias} to start")
                    code, detail = self._mci_locked(f"play {self._alias}")
                if code != 0:
                    raise RuntimeError(detail or f"audio play failed ({code})")
                started = time.monotonic()
        except Exception as exc:
            if self._stop.is_set():
                return
            logger.exception("SOS alarm playback failed")
            with self._lock:
                self._close_locked()
            callback = self.on_failure
            if callback is not None:
                callback(exc)

    def _ensure_api(self) -> None:
        if self._send is not None:
            return
        import ctypes

        send = ctypes.windll.winmm.mciSendStringW
        send.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        send.restype = ctypes.c_uint
        error_text = ctypes.windll.winmm.mciGetErrorStringW
        error_text.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        error_text.restype = ctypes.c_bool
        self._send = send
        self._error_text = error_text

    def _open_locked(self, path: str) -> None:
        wav = str(Path(path).resolve())
        if not Path(wav).is_file():
            raise RuntimeError(f"alarm sound missing: {wav}")
        if '"' in wav:
            raise RuntimeError("alarm sound path is not playable")
        self._close_locked()
        code, detail = self._mci_locked(f'open "{wav}" type waveaudio alias {self._alias}')
        if code != 0:
            raise RuntimeError(detail or f"audio open failed ({code})")
        self._open = True

    def _close_locked(self) -> None:
        if not self._open and self._send is None:
            return
        if self._send is None:
            self._open = False
            return
        self._mci_locked(f"stop {self._alias}")
        self._mci_locked(f"close {self._alias}")
        self._open = False

    def _status_locked(self) -> str:
        code, detail = self._mci_locked(f"status {self._alias} mode")
        if code != 0:
            return ""
        return (detail or "").strip().lower()

    def _mci_locked(self, command: str) -> tuple[int, str]:
        import ctypes

        self._ensure_api()
        buf = ctypes.create_unicode_buffer(260)
        code = self._send(command, buf, len(buf), None)
        if code == 0:
            return 0, buf.value
        err = ctypes.create_unicode_buffer(260)
        self._error_text(code, err, len(err))
        return code, err.value or f"MCI error {code}"


class CommandLoopPlayer:
    """Loop a local WAV with a system player. stop() terminates that process."""

    def __init__(self):
        self.on_failure = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    def play(self, path: str) -> None:
        command = _player_command(path)
        if not command:
            raise RuntimeError("no local audio player found")
        with self._lock:
            if self._thread and self._thread.is_alive() and not self._stop.is_set():
                return
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        with self._lock:
            if self._thread and self._thread.is_alive() and not self._stop.is_set():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, args=(command,), name="sos_alarm", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=0.5)

    def _loop(self, command: list[str]) -> None:
        try:
            while not self._stop.is_set():
                try:
                    proc = subprocess.Popen(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception as exc:
                    raise RuntimeError(str(exc)) from exc
                with self._lock:
                    self._proc = proc
                while proc.poll() is None:
                    if self._stop.wait(0.05):
                        self._terminate()
                        return
                if self._stop.wait(0.05):
                    return
        except Exception as exc:
            if self._stop.is_set():
                return
            logger.exception("SOS alarm playback failed")
            callback = self.on_failure
            if callback is not None:
                callback(exc)

    def _terminate(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()


def default_player():
    if sys.platform == "win32":
        return MciLoopPlayer()
    return CommandLoopPlayer()


def _player_command(path: str) -> list[str] | None:
    wav = str(Path(path).resolve())
    for tool, args in (
        ("afplay", ["afplay"]),
        ("paplay", ["paplay"]),
        ("aplay", ["aplay", "-q"]),
    ):
        if shutil.which(tool):
            return [*args, wav]
    return None


alarm = SOSAlarm()
atexit.register(alarm.stop)
