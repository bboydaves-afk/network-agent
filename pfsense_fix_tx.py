"""Fix igb1 TX: disable offloads, check pf blocked packets, try alt port."""

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
    print("  pfSense igb1 TX Debug & Fix")
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

    # 1. Check pf log for blocked packets on igb1
    print("[1] Last blocked packets on igb1 (from pf log):")
    out = run(ser, "clog /var/log/filter.log 2>/dev/null | grep igb1 | tail -10")
    if out.strip():
        for line in out.splitlines()[:10]:
            print(f"  {line.strip()[:120]}")
    else:
        print("  (no blocked packets logged)")
    print()

    # 2. Check pf out rules specifically
    print("[2] All OUT rules affecting igb1:")
    out = run(ser, "pfctl -sr | grep -E 'out.*igb1|out.*all'")
    if out.strip():
        for line in out.splitlines():
            print(f"  {line.strip()}")
    else:
        print("  (no out rules found)")
    print()

    # 3. Check igb0 TX counters for comparison
    print("[3] igb0 (WAN) packet counters for comparison:")
    out = run(ser, "netstat -I igb0 -b")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 4. Try disabling offloads
    print("[4] Disabling hardware offloads on igb1...")
    run(ser, "ifconfig igb1 -txcsum -rxcsum -tso4 -tso6 -lro")
    time.sleep(1)
    out = run(ser, "ifconfig igb1 | grep options")
    print(f"  {out.strip()}")
    print()

    # 5. Get pre-test packet count
    print("[5] Packet counters before test:")
    out = run(ser, "netstat -I igb1 -b | tail -1")
    print(f"  {out.strip()}")
    print()

    # 6. Try ARP who-has to see if TX works now
    print("[6] Sending ARP requests on igb1...")
    out = run(ser, "arping -c 3 -I igb1 192.168.1.1", timeout=10)
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 7. Check counters after
    print("[7] Packet counters after ARP test:")
    out = run(ser, "netstat -I igb1 -b | tail -1")
    print(f"  {out.strip()}")
    print()

    # 8. Try pinging from pfSense to a LAN address
    print("[8] Ping test to 192.168.1.100:")
    out = run(ser, "ping -c 2 -t 1 192.168.1.100", timeout=8)
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 9. If still not working, check sysctl net.inet settings
    print("[9] Checking IP forwarding:")
    out = run(ser, "sysctl net.inet.ip.forwarding")
    print(f"  {out.strip()}")
    print()

    # 10. Check interface MTU and other settings
    print("[10] igb1 sysctl tuning:")
    out = run(ser, "sysctl -a 2>/dev/null | grep igb1 | head -10")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 11. Check dmesg for errors
    print("[11] dmesg igb1 errors:")
    out = run(ser, "dmesg | grep -i 'igb1.*err\\|igb1.*fail\\|igb1.*timeout'")
    if out.strip():
        for line in out.splitlines():
            print(f"  {line.strip()}")
    else:
        print("  (no errors)")
    print()

    # 12. Final counters
    print("[12] Final packet counters:")
    out = run(ser, "netstat -I igb1 -b")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 13. Try completely restarting networking
    print("[13] Restarting pfSense networking...")
    out = run(ser, "pfSsh.php playback svc restart apinger", timeout=15)
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # Re-enable offloads if they don't help, as some NICs need them
    # Actually, let's leave them off and see

    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    print("[*] Interface status:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("=" * 55)
    print("  After these changes, try on your PC:")
    print("  1. Unplug and replug the Ethernet cable")
    print("  2. ipconfig /release && ipconfig /renew")
    print("  3. Browse to https://192.168.1.254")
    print("=" * 55)


if __name__ == "__main__":
    main()
