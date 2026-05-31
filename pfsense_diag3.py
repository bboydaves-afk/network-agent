"""pfSense LAN diagnostics with TERM=dumb to suppress resizewin noise."""

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
    """Run command, wait for prompt, return output lines only."""
    ser.reset_input_buffer()
    send(ser, cmd + "\n", delay=0.3)
    # Wait for next shell prompt
    resp = read_until(ser, r"#\s*$", timeout=timeout)
    c = clean(resp)
    # Remove command echo and prompt lines
    lines = []
    for line in c.splitlines():
        s = line.strip()
        if not s:
            continue
        if s == cmd or s.startswith(cmd[:30]):
            continue
        if re.match(r"\[2\.8\.\d.*\].*root@.*:", s):
            continue
        if "resizewin" in s:
            continue
        if s == "78":
            continue
        lines.append(s)
    return "\n".join(lines)


def main():
    print("=" * 55)
    print("  pfSense LAN Diagnostics (clean)")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Get to menu
    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=10)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    print("[*] Current state:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")
    print()

    # Enter shell
    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)

    # Disable resizewin by setting TERM=dumb
    print("[*] Setting TERM=dumb to suppress terminal noise...")
    run(ser, "export TERM=dumb")
    run(ser, "stty columns 200")
    time.sleep(1)

    # Now run diagnostics
    print()
    print("[1] igb1 full status:")
    out = run(ser, "ifconfig igb1")
    print(out if out else "  (no output)")
    print()

    print("[2] igb1 link status:")
    out = run(ser, "ifconfig igb1 | grep status")
    print(out if out else "  (no output)")
    print()

    print("[3] All NICs link status:")
    out = run(ser, "for i in igb0 igb1 igb2 igb3 igb4 igb5; do echo -n \"$i: \"; ifconfig $i 2>/dev/null | grep status; done")
    print(out if out else "  (no output)")
    print()

    print("[4] ARP table (igb1):")
    out = run(ser, "arp -an | grep igb1")
    print(out if out else "  (no ARP entries on igb1 - no clients connected)")
    print()

    print("[5] DHCP leases:")
    out = run(ser, "cat /var/dhcpd/var/db/dhcpd.leases 2>/dev/null")
    print(out if out else "  (no leases)")
    print()

    print("[6] Self-test curl to LAN IP:")
    out = run(ser, "curl -kso /dev/null -w '%{http_code}' https://192.168.1.254", timeout=10)
    print(out if out else "  (no response)")
    print()

    print("[7] nginx listening:")
    out = run(ser, "sockstat -4l | grep -E '443|80'")
    print(out if out else "  (no output)")
    print()

    print("[8] pfctl LAN rules:")
    out = run(ser, "pfctl -sr | grep igb1")
    print(out if out else "  (no rules on igb1)")
    print()

    print("[9] igb1 packet counters:")
    out = run(ser, "netstat -I igb1 -b")
    print(out if out else "  (no output)")
    print()

    print("[10] DHCP server running:")
    out = run(ser, "ps aux | grep dhcpd | grep -v grep")
    print(out if out else "  (dhcpd not running!)")
    print()

    # Exit
    send(ser, "exit\n", delay=2)
    ser.close()

    print("=" * 55)


if __name__ == "__main__":
    main()
