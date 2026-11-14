"""radio.py - talks to a Wio-E5 board in TEST mode. Same file on both laptops."""
import re, threading, time
import serial

RFCFG = "AT+TEST=RFCFG,915,SF7,125,12,15,14,ON,OFF,OFF"
MAX_LEN = 80
BAD = '|",\r\n\\'


def sanitize(text, limit=MAX_LEN):
    """Strip characters that could break the message format or the AT command."""
    return "".join(c for c in str(text) if c not in BAD).strip()[:limit]
def clean_packet(text):
    """For the whole packet: keep | separators, strip only what breaks the AT command."""
    return "".join(c for c in str(text) if c not in '"\r\n\\')[:MAX_LEN]

class Radio:
    def __init__(self, port, baud=9600):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.lock = threading.Lock()
        self._stop = False

    def cmd(self, command, wait_for=None, timeout=3.0):
        """Send one AT command, return the reply lines. Stops early at wait_for."""
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write((command + "\r\n").encode())
            lines, end = [], time.time() + timeout
            while time.time() < end:
                raw = self.ser.readline().decode(errors="replace").strip()
                if raw:
                    lines.append(raw)
                    if wait_for and wait_for in raw:
                        break
            return lines

    def setup(self, receiver=False):
        if not any("OK" in l for l in self.cmd("AT")):
            raise RuntimeError("Board did not answer AT. Check port, cable, baud.")
        self.cmd("AT+MODE=TEST", "TEST")
        self.cmd(RFCFG, "RFCFG")
        if receiver:
            self.cmd("AT+TEST=RXLRPKT", "RXLRPKT")

    def send(self, text):
        """Transmit text. Returns True if the board reports TX DONE."""
        text = clean_packet(text)
        lines = self.cmd(f'AT+TEST=TXLRSTR,"{text}"', "TX DONE", timeout=5)
        return any("TX DONE" in l for l in lines)

    def listen(self, on_message, rearm_after=30):
        """Run in a thread (Board B). Calls on_message(text, rssi) per packet."""
        rssi, last = None, time.time()
        while not self._stop:
            try:
                with self.lock:
                    raw = self.ser.readline().decode(errors="replace").strip()
            except serial.SerialException:
                time.sleep(1); continue
            if raw:
                m = re.search(r"RSSI:(-?\d+)", raw)
                if m: rssi = int(m.group(1))
                m = re.search(r'RX "([0-9A-Fa-f]+)"', raw)
                if m:
                    last = time.time()
                    try:
                        on_message(bytes.fromhex(m.group(1)).decode("utf-8", "replace"), rssi)
                    except Exception as e:
                        print("message handler error:", e)
            if time.time() - last > rearm_after:   # board can drop out of RX mode
                self.cmd("AT+TEST=RXLRPKT", "RXLRPKT", timeout=1)
                last = time.time()

    def stop(self):
        self._stop = True

if __name__ == "__main__":   # quick test: python radio.py COM5 A   |   python radio.py COM6 B
    import sys
    port, role = sys.argv[1], sys.argv[2].upper()
    r = Radio(port); r.setup(receiver=(role == "B"))
    if role == "A":
        print("sent:", r.send("1|F14|MED|radio test"))
    else:
        print("listening..."); r.listen(lambda t, s: print("GOT:", t, "RSSI", s))
