"""Capture actual traffic on igb2 to see what's happening."""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")


def clean(text):
    return ANSI.sub("", text)

def send(ser, text, delay=0.5):
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(delay)

def read_until(ser, pattern, timeout=30):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            buf += chunk
            c = clean(buf)
            if re.search(pattern, c, re.IGNORECASE):
                return c
        else:
            time.sleep(0.2)
    return clean(buf)

def run(ser, cmd, timeout=10):
    ser.reset_input_buffer()
    send(ser, cmd + "\n", delay=0.3)
    resp = read_until(ser, r"#\s*$", timeout=timeout)
    c = clean(resp)
    lines = []
    for line in c.splitlines():
        s = line.strip()
        if not s or "resizewin" in s or s == "78":
            continue
        if re.match(r"\[2\.8\.\d.*\].*root@", s):
            continue
        if s == cmd or s.startswith(cmd[:25]):
            continue
        lines.append(s)
    return "\n".join(lines)


def main():
    print("=" * 55)
    print("  tcpdump on igb2 - live traffic capture")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=10)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")

    # Check current counters
    print("[1] Current igb2 counters:")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    # Run tcpdump for 15 seconds
    print("[2] Capturing traffic on igb2 for 15 seconds...")
    print("  (try pinging 192.168.1.254 from your PC)")
    print()

    ser.reset_input_buffer()
    send(ser, "tcpdump -n -e -i igb2 -c 30 2>&1 &\n", delay=1)
    time.sleep(1)

    # Also try a ping from pfSense side
    time.sleep(3)
    run(ser, "ping -c 1 -t 1 192.168.1.100 2>/dev/null", timeout=5)

    # Wait for tcpdump to finish or timeout
    time.sleep(12)

    # Kill tcpdump
    run(ser, "killall tcpdump 2>/dev/null")
    time.sleep(2)

    # Read all output
    buf = ""
    end = time.time() + 3
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            time.sleep(0.3)
        else:
            time.sleep(0.3)
    cleaned = clean(buf)
    for line in cleaned.splitlines():
        s = line.strip()
        if s and "resizewin" not in s and s != "78" and not s.startswith("[2.8"):
            print(f"  {s[:140]}")
    print()

    # Check counters after
    print("[3] Counters after capture:")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    # Also check: what does the kernel see?
    print("[4] Kernel network stats for igb2:")
    out = run(ser, "sysctl dev.igb.2.mac_stats.tx_frames 2>/dev/null")
    print(f"  {out.strip()}")
    out = run(ser, "sysctl dev.igb.2.mac_stats.rx_frames 2>/dev/null")
    print(f"  {out.strip()}")
    print()

    # Check if there's a bridge
    print("[5] Bridge interfaces:")
    out = run(ser, "ifconfig -a | grep bridge")
    print(f"  {out.strip() if out.strip() else '(none)'}")
    print()

    # Check for any interface groups
    print("[6] Interface groups:")
    out = run(ser, "ifconfig -g")
    for line in out.splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    print()

    send(ser, "exit\n", delay=2)
    ser.close()

    print("=" * 55)


if __name__ == "__main__":
    main()
