"""Test: temporarily disable PF firewall to isolate TX issue."""

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
    print("  PF Firewall Disable Test")
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

    # Disable offloads
    run(ser, "ifconfig igb2 -txcsum -rxcsum -tso4 -tso6 -lro 2>/dev/null")

    # Get baseline
    print("[1] Baseline TX counters:")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    # Disable PF entirely
    print("[2] DISABLING packet filter (pfctl -d)...")
    out = run(ser, "pfctl -d")
    for line in out.splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    print()

    # Try ping
    print("[3] Ping test with PF disabled:")
    out = run(ser, "ping -c 3 -t 2 192.168.1.255", timeout=12)
    for line in out.splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    print()

    # Check counters
    print("[4] TX counters after ping (PF disabled):")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    # ARP test
    print("[5] ARP table:")
    out = run(ser, "arp -an | grep igb2")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # Try a direct ARP ping
    print("[6] Direct ping to common IPs on LAN:")
    for ip in ["192.168.1.1", "192.168.1.100", "192.168.1.101"]:
        out = run(ser, f"ping -c 1 -t 1 {ip}", timeout=5)
        for line in out.splitlines():
            if "transmitted" in line:
                print(f"  {ip}: {line.strip()}")
    print()

    # Check counters again
    print("[7] TX counters after all pings:")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    # Wait for DHCP with PF disabled
    print("[8] Waiting 10s for DHCP (PF disabled)...")
    print("  >>> Run ipconfig /renew on your PC NOW <<<")
    time.sleep(10)

    print("[9] Counters after DHCP wait:")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    print("[10] ARP table:")
    out = run(ser, "arp -an | grep igb2")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # Check tcpdump for any traffic
    print("[11] Capturing 5 seconds of igb2 traffic...")
    out = run(ser, "timeout 5 tcpdump -c 20 -n -i igb2 2>&1", timeout=10)
    for line in out.splitlines():
        s = line.strip()
        if s:
            print(f"  {s[:130]}")
    print()

    # Re-enable PF
    print("[12] Re-enabling packet filter...")
    out = run(ser, "pfctl -e")
    for line in out.splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    run(ser, "php -r 'require_once(\"config.inc\"); require_once(\"filter.inc\"); filter_configure();'", timeout=15)
    print("  PF re-enabled.")
    print()

    send(ser, "exit\n", delay=2)
    ser.close()

    print("=" * 55)


if __name__ == "__main__":
    main()
