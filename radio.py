"""LoRa transport: real Wio-E5 (AT firmware, P2P test mode) or a UDP fake for simulation."""
import logging
import os
import random
import re
import socket
import threading
import time
from datetime import datetime

import serial
from dotenv import load_dotenv

log = logging.getLogger("radio")

RFCFG = "AT+TEST=RFCFG,915,SF7,125,12,15,14,ON,OFF,OFF"
RX_IDLE_REARM_S = 60
RECONNECT_S = 2
TX_TIMEOUT_S = 3

_LEN_RE = re.compile(r"LEN:(\d+), RSSI:(-?\d+), SNR:(-?[\d.]+)")
_RX_RE = re.compile(r'\+TEST: RX "([0-9A-Fa-f]*)"')


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BaseRadio:
    mode = "base"

    def __init__(self):
        self.last_rx: str | None = None
        self.error: str | None = None

    def send(self, payload: str) -> bool:
        raise NotImplementedError

    def listen(self, on_packet) -> None:
        raise NotImplementedError

    def health(self) -> dict:
        return {"ok": self.error is None, "mode": self.mode, "last_rx": self.last_rx, "error": self.error}

    def close(self) -> None:
        pass


class WioE5(BaseRadio):
    mode = "wio"

    def __init__(self, port: str, baud: int = 9600):
        super().__init__()
        self.port, self.baud = port, baud
        self.ser: serial.Serial | None = None
        self.lock = threading.Lock()
        self._connect()

    def _connect(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
        self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
        self.ser.reset_input_buffer()
        self._cmd("AT", "+AT: OK")
        self._cmd("AT+MODE=TEST", "+MODE: TEST")
        self._cmd(RFCFG, "RFCFG")
        self.error = None
        log.info("%s ready in TEST mode (%s)", self.port, RFCFG)

    def _readline(self) -> str:
        return self.ser.readline().decode(errors="replace").strip()

    def _cmd(self, cmd: str, expect: str, timeout: float = 1.5) -> list[str]:
        self.ser.write((cmd + "\r\n").encode())
        lines, end = [], time.time() + timeout
        while time.time() < end:
            line = self._readline()
            if not line:
                continue
            lines.append(line)
            if "ERROR" in line:
                raise RuntimeError(f"{cmd} -> {line}")
            if expect in line:
                return lines
        raise TimeoutError(f"{cmd}: no {expect!r} within {timeout}s (got {lines})")

    def send(self, payload: str) -> bool:
        with self.lock:
            try:
                self._cmd(f'AT+TEST=TXLRPKT "{payload.encode().hex().upper()}"', "TX DONE", TX_TIMEOUT_S)
                self.error = None
                return True
            except (serial.SerialException, OSError, RuntimeError, TimeoutError) as e:
                self.error = f"send failed: {e}"
                log.warning(self.error)
                try:
                    self._connect()
                except Exception as e2:
                    log.warning("reconnect failed: %s", e2)
                return False

    def _arm_rx(self) -> None:
        self._cmd("AT+TEST=RXLRPKT", "RXLRPKT")

    def listen(self, on_packet) -> None:
        while True:
            try:
                with self.lock:
                    self._arm_rx()
                log.info("%s RX mode on", self.port)
                self._rx_loop(on_packet)
            except (serial.SerialException, OSError, RuntimeError, TimeoutError) as e:
                self.error = f"rx: {e}"
                log.warning("%s; reconnecting in %ss", self.error, RECONNECT_S)
                time.sleep(RECONNECT_S)
                try:
                    with self.lock:
                        self._connect()
                except Exception as e2:
                    self.error = f"reconnect failed: {e2}"
                    log.warning(self.error)

    def _rx_loop(self, on_packet) -> None:
        rssi = snr = None
        last_activity = time.time()
        while True:
            line = self._readline()
            if not line:
                if time.time() - last_activity > RX_IDLE_REARM_S:
                    with self.lock:
                        self._arm_rx()
                    log.info("%s RX re-armed after %ss idle", self.port, RX_IDLE_REARM_S)
                    last_activity = time.time()
                continue
            last_activity = time.time()
            if m := _LEN_RE.search(line):
                rssi, snr = int(m.group(2)), float(m.group(3))
            elif m := _RX_RE.search(line):
                try:
                    payload = bytes.fromhex(m.group(1)).decode("ascii", errors="replace")
                except ValueError:
                    log.warning("bad hex from radio: %s", line)
                    continue
                self.last_rx, self.error = _now(), None
                self._deliver(on_packet, payload, rssi, snr)
                rssi = snr = None
            elif "ERROR" in line:
                raise RuntimeError(line)

    @staticmethod
    def _deliver(on_packet, payload, rssi, snr) -> None:
        try:
            on_packet(payload, rssi, snr)
        except Exception:
            log.exception("on_packet failed for %r", payload)

    def close(self) -> None:
        if self.ser:
            self.ser.close()


class FakeRadio(BaseRadio):
    """UDP stand-in for LoRa. Sender and receiver must use the same SIM_HOST/SIM_PORT."""

    mode = "sim"

    def __init__(self, host: str = "127.0.0.1", port: int = 47000):
        super().__init__()
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, payload: str) -> bool:
        self.sock.sendto(payload.encode(), self.addr)
        return True

    def listen(self, on_packet) -> None:
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("0.0.0.0", self.addr[1]))
        log.info("FakeRadio listening on UDP %s", self.addr[1])
        while True:
            data, _ = rx.recvfrom(1024)
            self.last_rx = _now()
            WioE5._deliver(on_packet, data.decode(errors="replace"), -random.randint(45, 110), round(random.uniform(-5, 10), 1))

    def close(self) -> None:
        self.sock.close()


def get_radio() -> BaseRadio:
    load_dotenv()
    if os.getenv("SIMULATE", "0") == "1":
        return FakeRadio(os.getenv("SIM_HOST", "127.0.0.1"), int(os.getenv("SIM_PORT", "47000")))
    return WioE5(os.getenv("RADIO_PORT", "COM7"))
