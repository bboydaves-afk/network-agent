"""Verify igb2 link and TX after cable move."""

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
    print("  Verify igb2 (port 3) connectivity")
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

    print("[*] Menu state:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")
    print()

    # Enter shell
    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")

    # Disable offloads on igb2
    run(ser, "ifconfig igb2 -txcsum -rxcsum -tso4 -tso6 -lro 2>/dev/null")
    time.sleep(1)

    # Check link
    print("[1] igb2 link status:")
    out = run(ser, "ifconfig igb2 | grep -E 'status|flags|inet|media'")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # Pre-test counters
    print("[2] Packet counters:")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    # Ping broadcast
    print("[3] Ping broadcast test:")
    out = run(ser, "ping -c 3 -t 2 192.168.1.255", timeout=12)
    for line in out.splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    print()

    # Post-test counters
    print("[4] Packet counters after ping:")
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  {out.strip()}")
    print()

    # ARP table
    print("[5] ARP table:")
    out = run(ser, "arp -an | grep igb2")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # DHCP leases
    print("[6] DHCP leases:")
    out = run(ser, "cat /var/dhcpd/var/db/dhcpd.leases 2>/dev/null")
    if out.strip() and "lease" in out.lower():
        for line in out.splitlines():
            print(f"  {line.strip()}")
    else:
        print("  (no leases yet)")
    print()

    # Curl self-test
    print("[7] WebGUI self-test:")
    out = run(ser, "curl -kso /dev/null -w '%{http_code}' https://192.168.1.254", timeout=10)
    for line in out.splitlines():
        s = line.strip()
        if s:
            print(f"  HTTP response: {s}")
    print()

    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    ser.close()

    print("=" * 55)
    print("  On your PC run: ipconfig /release && ipconfig /renew")
    print("  Then browse to: https://192.168.1.254")
    print("=" * 55)


if __name__ == "__main__":
    main()
