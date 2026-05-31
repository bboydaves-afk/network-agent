"""Fix igb1 TX issue - interface receives but won't transmit."""

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
        if s == cmd:
            continue
        lines.append(s)
    return "\n".join(lines)


def show(text, prefix="  "):
    for line in text.strip().splitlines():
        safe = line.encode("ascii", errors="replace").decode("ascii")
        if safe.strip():
            print(f"{prefix}{safe}")


def main():
    print("=" * 55)
    print("  pfSense igb1 TX Fix")
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

    # Enter shell
    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")
    time.sleep(0.5)

    # Check config.xml for malformed LAN XML
    print("[1] Checking config.xml LAN section for issues...")
    out = run(ser, "sed -n '/<interfaces>/,/<\\/interfaces>/p' /cf/conf/config.xml")
    show(out)
    print()

    # Check if there's a duplicate/nested <lan> tag
    print("[2] Checking for malformed XML...")
    out = run(ser, "grep -n 'lan' /cf/conf/config.xml")
    show(out)
    print()

    # Try bouncing the interface
    print("[3] Bouncing igb1 (down then up)...")
    run(ser, "ifconfig igb1 down")
    time.sleep(2)
    run(ser, "ifconfig igb1 up")
    time.sleep(2)

    # Re-assign IP
    print("[4] Re-assigning IP to igb1...")
    run(ser, "ifconfig igb1 inet 192.168.1.254 netmask 255.255.255.0")
    time.sleep(1)

    # Check status
    out = run(ser, "ifconfig igb1")
    show(out)
    print()

    # Try to send a gratuitous ARP
    print("[5] Sending gratuitous ARP on igb1...")
    run(ser, "arping -c 3 -I igb1 192.168.1.254", timeout=10)
    time.sleep(1)

    # Check packet counters now
    print("[6] Packet counters after bounce:")
    out = run(ser, "netstat -I igb1 -b")
    show(out)
    print()

    # Check if there's a broken pf state blocking output
    print("[7] Clearing pf states on igb1...")
    run(ser, "pfctl -Fs")
    time.sleep(1)

    # Full PHP interface reconfigure
    print("[8] Reconfiguring LAN interface via PHP...")
    out = run(ser, "pfSsh.php playback svc restart dhcpd", timeout=30)
    show(out)
    print()

    print("[9] Reloading all interfaces via PHP...")
    php = 'php -r \'require_once("config.inc"); require_once("interfaces.inc"); interface_configure("lan"); echo "done\\n";\''
    out = run(ser, php, timeout=20)
    show(out)
    print()

    # Check counters again
    print("[10] Final packet counters:")
    out = run(ser, "netstat -I igb1 -b")
    show(out)
    print()

    # Test: send a ping to broadcast on LAN
    print("[11] Ping broadcast on LAN subnet:")
    out = run(ser, "ping -c 2 -t 1 192.168.1.255", timeout=8)
    show(out)
    print()

    # Check ARP table
    print("[12] ARP table after fixes:")
    out = run(ser, "arp -an | grep igb1")
    show(out)
    print()

    # Check link and counters one more time
    print("[13] igb1 final status:")
    out = run(ser, "ifconfig igb1 | grep -E 'status|flags|inet|ether'")
    show(out)
    print()

    # Exit
    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    # Show menu
    print("[*] Menu:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("=" * 55)
    print("  Try accessing https://192.168.1.254 now.")
    print("  If your PC still has no IP, try disconnecting")
    print("  and reconnecting the cable, or run:")
    print("    ipconfig /release && ipconfig /renew")
    print("=" * 55)


if __name__ == "__main__":
    main()
