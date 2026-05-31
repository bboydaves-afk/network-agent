"""Deep diagnostics for pfSense LAN connectivity."""

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


def read_all_wait(ser, timeout=5):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            end = time.time() + 1.5
        else:
            time.sleep(0.2)
    return clean(buf)


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


def run(ser, cmd, timeout=8):
    """Run a shell command and return clean output."""
    ser.reset_input_buffer()
    send(ser, cmd + "\n", delay=0.3)
    time.sleep(1.5)
    resp = read_all_wait(ser, timeout=timeout)
    # Filter out noise
    lines = []
    for line in resp.splitlines():
        s = line.strip()
        if s and "resizewin" not in s and s != "78" and not s.startswith("[2.8.1"):
            # Skip the command echo
            if s != cmd and not s.startswith(cmd[:20]):
                lines.append(s)
    return "\n".join(lines)


def show(text, indent="  "):
    for line in text.strip().splitlines():
        safe = line.encode("ascii", errors="replace").decode("ascii")
        if safe.strip():
            print(f"{indent}{safe}")


def main():
    print("=" * 55)
    print("  pfSense LAN Deep Diagnostics")
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

    # 1. igb1 full status
    print("[1] igb1 interface details:")
    out = run(ser, "ifconfig igb1", timeout=8)
    show(out)
    print()

    # 2. igb1 media / link
    print("[2] igb1 media status:")
    out = run(ser, "ifconfig igb1 | grep -E 'status|media'", timeout=5)
    show(out)
    print()

    # 3. ARP table - any clients?
    print("[3] ARP table (LAN clients):")
    out = run(ser, "arp -an | grep igb1", timeout=5)
    if out.strip():
        show(out)
    else:
        print("  (empty - no devices seen on igb1)")
    print()

    # 4. DHCP leases
    print("[4] DHCP leases:")
    out = run(ser, "cat /var/dhcpd/var/db/dhcpd.leases", timeout=5)
    if out.strip():
        show(out)
    else:
        print("  (no leases)")
    print()

    # 5. Ping the LAN IP from pfSense itself
    print("[5] Self-ping 192.168.1.254:")
    out = run(ser, "ping -c 2 192.168.1.254", timeout=8)
    show(out)
    print()

    # 6. nginx status
    print("[6] nginx process:")
    out = run(ser, "ps aux | grep nginx | grep -v grep", timeout=5)
    show(out)
    print()

    # 7. Test binding on LAN IP specifically
    print("[7] Connections listening on 192.168.1.254:")
    out = run(ser, "sockstat -l4 | grep 192.168.1.254", timeout=5)
    if out.strip():
        show(out)
    else:
        print("  (none specifically on 192.168.1.254)")
        print("  Checking wildcard listeners:")
        out = run(ser, "sockstat -l4 | grep '\\*:443\\|\\*:80'", timeout=5)
        show(out)
    print()

    # 8. Check if pfSense webgui is bound to specific interface
    print("[8] WebGUI bind check:")
    out = run(ser, "grep -A2 'webgui' /cf/conf/config.xml | grep -i 'interface\\|listen'", timeout=5)
    if out.strip():
        show(out)
    else:
        print("  (no interface restriction - listens on all)")
    print()

    # 9. Try curl from pfSense to its own LAN IP
    print("[9] curl test to https://192.168.1.254:")
    out = run(ser, "curl -ksI https://192.168.1.254 2>&1 | head -5", timeout=10)
    show(out)
    print()

    # 10. Check dmesg for igb1 link changes
    print("[10] Recent igb1 link events:")
    out = run(ser, "dmesg | grep -i igb1 | tail -5", timeout=5)
    if out.strip():
        show(out)
    else:
        print("  (no recent igb1 events)")
    print()

    # 11. Check which physical port is igb1
    print("[11] NIC PCI mapping:")
    out = run(ser, "pciconf -lv | grep -B3 -A1 igb1", timeout=5)
    if out.strip():
        show(out)
    else:
        out = run(ser, "dmesg | grep 'igb[0-5]:.*port'", timeout=5)
        if out.strip():
            show(out)
        else:
            out = run(ser, "dmesg | grep 'igb[0-5].*mem'", timeout=5)
            show(out)
    print()

    # Exit
    send(ser, "exit\n", delay=2)
    ser.close()

    print("=" * 55)
    print("  Diagnostics complete.")
    print("=" * 55)


if __name__ == "__main__":
    main()
