"""Fix DHCP and firewall rules for igb2 after interface reassignment."""

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
    print("  Fix services for igb2")
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

    # 1. Check which interface DHCP is bound to
    print("[1] DHCP server binding:")
    out = run(ser, "ps aux | grep dhcpd | grep -v grep")
    for line in out.splitlines():
        s = line.strip()
        if "dhcpd" in s:
            # Find the interface at the end
            if "igb" in s:
                iface = s[s.rfind("igb"):]
                print(f"  Bound to: {iface}")
            else:
                print(f"  {s}")
    print()

    # 2. Check DHCP config
    print("[2] DHCP config file:")
    out = run(ser, "cat /var/dhcpd/etc/dhcpd.conf 2>/dev/null | head -20")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 3. Check firewall rules for igb2
    print("[3] Firewall rules for igb2:")
    out = run(ser, "pfctl -sr | grep igb2")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 4. Restart DHCP to bind to igb2
    print("[4] Restarting DHCP server...")
    out = run(ser, "pfSsh.php playback svc restart dhcpd", timeout=20)
    for line in out.splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    print()

    # 5. Verify DHCP is now on igb2
    print("[5] DHCP server after restart:")
    out = run(ser, "ps aux | grep dhcpd | grep -v grep")
    for line in out.splitlines():
        s = line.strip()
        if "dhcpd" in s:
            if "igb" in s:
                iface = s[s.rfind("igb"):]
                print(f"  Bound to: {iface}")
    print()

    # 6. Regenerate firewall rules
    print("[6] Regenerating firewall rules...")
    php = "php -r 'require_once(\"config.inc\"); require_once(\"filter.inc\"); filter_configure(); echo \"done\\n\";'"
    out = run(ser, php, timeout=20)
    for line in out.splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    print()

    # 7. Check rules again
    print("[7] Firewall rules for igb2 after reload:")
    out = run(ser, "pfctl -sr | grep igb2")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 8. Check igb2 link status
    print("[8] igb2 status:")
    out = run(ser, "ifconfig igb2 | grep -E 'status|inet '")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 9. Pre-test counters
    out = run(ser, "netstat -I igb2 -b | grep Link")
    pre_tx = out.strip()
    print(f"[9] Pre-test: {pre_tx}")
    print()

    # 10. Wait for DHCP traffic
    print("[10] Waiting 10 seconds for DHCP...")
    print("  On your PC run: ipconfig /release && ipconfig /renew")
    time.sleep(10)

    # 11. Post-test
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"[11] Post-test: {out.strip()}")
    print()

    # 12. ARP table
    print("[12] ARP table:")
    out = run(ser, "arp -an | grep igb2")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 13. DHCP leases
    print("[13] DHCP leases:")
    out = run(ser, "cat /var/dhcpd/var/db/dhcpd.leases 2>/dev/null")
    if out.strip() and len(out.strip()) > 5:
        for line in out.splitlines():
            print(f"  {line.strip()}")
    else:
        print("  (no leases)")
    print()

    # 14. Check DHCP log
    print("[14] DHCP log entries:")
    out = run(ser, "clog /var/log/dhcpd.log 2>/dev/null | tail -10")
    for line in out.splitlines():
        s = line.strip()
        if s:
            print(f"  {s[:120]}")
    print()

    send(ser, "exit\n", delay=2)
    ser.close()

    print("=" * 55)


if __name__ == "__main__":
    main()
