"""Fix Aruba switch VLAN 50 IP to match pfSense VLAN 50 subnet."""

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


def read_all(ser, timeout=3):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            buf += chunk
            time.sleep(0.2)
        else:
            time.sleep(0.3)
    return clean(buf)


def read_until(ser, pattern, timeout=10):
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


def send_cmd(ser, cmd, wait=2):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("utf-8"))
    ser.flush()
    time.sleep(wait)
    buf = ""
    while ser.in_waiting:
        buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
        time.sleep(0.2)
    return clean(buf)


def main():
    print("=" * 55)
    print("  Fix Aruba Switch VLAN 50 IP")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Wake up the switch - send multiple enters
    print("[*] Connecting to switch...")
    for i in range(5):
        ser.write(b"\r\n")
        ser.flush()
        time.sleep(1)

    resp = read_all(ser, timeout=5)
    print(f"  Got: {resp.strip()[-100:]}")

    # Handle Press any key / login
    if "Press any key" in resp or "continue" in resp.lower():
        ser.write(b"\r\n")
        ser.flush()
        time.sleep(2)
        resp = read_all(ser, timeout=3)

    if "Username" in resp or "login" in resp.lower():
        print("  Logging in...")
        ser.write(b"manager\r\n")
        ser.flush()
        time.sleep(1)
        resp = read_all(ser, timeout=3)
        if "Password" in resp:
            ser.write(b"Welcome01!\r\n")
            ser.flush()
            time.sleep(2)
            resp = read_all(ser, timeout=3)
        print(f"  {resp.strip()[-80:]}")
    elif "Password" in resp:
        ser.write(b"Welcome01!\r\n")
        ser.flush()
        time.sleep(2)
        resp = read_all(ser, timeout=3)

    # Check if we need enable mode
    if ">" in resp and "#" not in resp:
        ser.write(b"enable\r\n")
        ser.flush()
        time.sleep(1)
        resp = read_all(ser, timeout=2)
        if "Password" in resp:
            ser.write(b"\r\n")
            ser.flush()
            time.sleep(1)
            resp = read_all(ser, timeout=2)

    # Try to get to a known state
    resp = send_cmd(ser, "", wait=1)
    print(f"  Prompt: {resp.strip()[-60:]}")
    print()

    # Show current VLAN 50 config
    print("[1] Current VLAN 50 IP:")
    resp = send_cmd(ser, "show ip vlan 50", wait=3)
    for line in resp.splitlines():
        s = line.strip()
        if s and ("50" in s or "MGMT" in s or "10." in s or "192." in s or "address" in s.lower()):
            print(f"  {s}")
    if not any("50" in line or "192" in line or "10." in line for line in resp.splitlines()):
        print(f"  Raw: {resp.strip()[-200:]}")
    print()

    # Show current routes
    print("[2] Current default route:")
    resp = send_cmd(ser, "show ip route 0.0.0.0", wait=3)
    for line in resp.splitlines():
        s = line.strip()
        if s and ("0.0.0.0" in s or "gateway" in s.lower() or "static" in s.lower()):
            print(f"  {s}")
    print()

    # Enter config mode
    print("[3] Applying changes...")
    send_cmd(ser, "configure terminal", wait=1)

    # Change VLAN 50 IP
    send_cmd(ser, "vlan 50", wait=1)
    resp = send_cmd(ser, "no ip address 192.168.1.1 255.255.255.0", wait=1)
    print(f"  Removed old IP: 192.168.1.1/24")
    resp = send_cmd(ser, "ip address 10.50.50.1 255.255.255.0", wait=1)
    print(f"  Set new IP: 10.50.50.1/24")
    send_cmd(ser, "exit", wait=0.5)

    # Update default route
    resp = send_cmd(ser, "no ip route 0.0.0.0 0.0.0.0 192.168.1.254", wait=1)
    print(f"  Removed old default route via 192.168.1.254")
    resp = send_cmd(ser, "ip route 0.0.0.0 0.0.0.0 10.50.50.254", wait=1)
    print(f"  Set new default route via 10.50.50.254")

    # Exit config mode
    send_cmd(ser, "exit", wait=1)
    print()

    # Save
    print("[4] Saving configuration...")
    resp = send_cmd(ser, "write memory", wait=5)
    if "success" in resp.lower() or "done" in resp.lower() or "#" in resp:
        print("  Saved.")
    else:
        print(f"  {resp.strip()[-80:]}")
    print()

    # Verify
    print("[5] Verification:")
    resp = send_cmd(ser, "show ip vlan 50", wait=3)
    for line in resp.splitlines():
        s = line.strip()
        if s and ("50" in s or "10.50" in s or "MGMT" in s or "address" in s.lower()):
            print(f"  {s}")
    if not any("10.50" in line for line in resp.splitlines()):
        print(f"  Raw: {resp.strip()[-200:]}")

    print()
    resp = send_cmd(ser, "show ip route", wait=3)
    for line in resp.splitlines():
        s = line.strip()
        if s and ("0.0.0.0" in s or "10.50" in s or "default" in s.lower()):
            print(f"  {s}")

    ser.close()
    print()
    print("=" * 55)
    print("  VLAN 50: 192.168.1.1 -> 10.50.50.1/24")
    print("  Default route: -> 10.50.50.254 (pfSense)")
    print("=" * 55)


if __name__ == "__main__":
    main()
