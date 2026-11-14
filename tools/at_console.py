"""Manual AT terminal for a Wio-E5.

  python tools/at_console.py COM7            interactive: type AT commands, Ctrl+C to quit
  python tools/at_console.py COM7 COM8 --probe   send AT and AT+VER to each port and exit
"""
import sys
import threading
import time

import serial


def read_for(ser: serial.Serial, seconds: float) -> list[str]:
    lines, end = [], time.time() + seconds
    while time.time() < end:
        line = ser.readline().decode(errors="replace").strip()
        if line:
            lines.append(line)
    return lines


def probe(port: str) -> None:
    try:
        with serial.Serial(port, 9600, timeout=0.2) as ser:
            for cmd in ("AT", "AT+VER", "AT+MODE"):
                ser.write((cmd + "\r\n").encode())
                print(f"{port} > {cmd}: {read_for(ser, 0.8)}")
    except serial.SerialException as e:
        print(f"{port}: cannot open ({e}). Is the Arduino Serial Monitor open?")


def interactive(port: str) -> None:
    ser = serial.Serial(port, 9600, timeout=0.2)

    def reader():
        while ser.is_open:
            try:
                line = ser.readline().decode(errors="replace").strip()
            except serial.SerialException:
                return
            if line:
                print(f"< {line}")

    threading.Thread(target=reader, daemon=True).start()
    print(f"Connected to {port}. Type AT commands (Ctrl+C to quit).")
    try:
        while True:
            ser.write((input() + "\r\n").encode())
    except (KeyboardInterrupt, EOFError):
        ser.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    if "--probe" in sys.argv:
        for p in args:
            probe(p)
    else:
        interactive(args[0])
