"""Diagnose pfSense LAN connectivity issues."""

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


def send(ser, text, delay=0.5):
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(delay)


def shell_cmd(ser, cmd, timeout=10):
    send(ser, cmd + "\n", delay=0.5)
    resp = read_until(ser, r"\[2\.8\.\d.*\].*#|root@", timeout=timeout)
    return resp


def show(resp):
    for line in resp.strip().splitlines():
        safe = line.encode("ascii", errors="replace").decode("ascii").strip()
        if safe and "resizewin" not in safe:
            print(f"  {safe}")


def main():
    print("=" * 55)
    print("  pfSense LAN Diagnostics")
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

    if "Enter an option:" not in resp:
        print("[!] Cannot reach pfSense menu.")
        ser.close()
        return

    # Show current interfaces
    print("[*] Current interfaces:")
    for line in resp.strip().splitlines():
        s = line.encode("ascii", errors="replace").decode("ascii").strip()
        if "->" in s:
            print(f"  {s}")
    print()

    # Enter shell
    print("[*] Entering shell for diagnostics...")
    send(ser, "8\n", delay=2)
    read_until(ser, r"\[2\.8|root@", timeout=10)

    # Check igb1 link state
    print("[1] igb1 interface status:")
    resp = shell_cmd(ser, "ifconfig igb1")
    show(resp)
    print()

    # Check if igb1 has an IP
    print("[2] igb1 IP addresses:")
    resp = shell_cmd(ser, "ifconfig igb1 | grep inet")
    show(resp)
    print()

    # Check link state of all interfaces
    print("[3] All interface link states:")
    resp = shell_cmd(ser, "ifconfig -a | grep -E '^igb|status:'")
    show(resp)
    print()

    # Check firewall rules on LAN
    print("[4] Firewall rules (LAN):")
    resp = shell_cmd(ser, "pfctl -sr | grep -i lan", timeout=10)
    show(resp)
    print()

    # Check all pfctl rules briefly
    print("[5] All active firewall rules:")
    resp = shell_cmd(ser, "pfctl -sr | head -30", timeout=10)
    show(resp)
    print()

    # Check if webgui is listening
    print("[6] Web server listening ports:")
    resp = shell_cmd(ser, "sockstat -l | grep -E 'nginx|php|httpd|443|80'", timeout=10)
    show(resp)
    print()

    # Check routing table
    print("[7] Routing table:")
    resp = shell_cmd(ser, "netstat -rn | head -15", timeout=10)
    show(resp)
    print()

    # Check DHCP server
    print("[8] DHCP server status:")
    resp = shell_cmd(ser, "ps aux | grep dhcpd", timeout=10)
    show(resp)
    print()

    # Check config.xml LAN section
    print("[9] config.xml LAN section:")
    resp = shell_cmd(ser, "grep -A10 '<lan>' /cf/conf/config.xml", timeout=10)
    show(resp)
    print()

    # Check anti-lockout rule
    print("[10] Anti-lockout rule:")
    resp = shell_cmd(ser, "grep -i 'antilockout\\|anti-lockout\\|webgui' /cf/conf/config.xml | head -5", timeout=10)
    show(resp)
    print()

    # Exit shell
    send(ser, "exit\n", delay=2)
    read_until(ser, r"Enter an option:", timeout=10)

    ser.close()
    print("=" * 55)
    print("  Diagnostics complete.")
    print("=" * 55)


if __name__ == "__main__":
    main()
