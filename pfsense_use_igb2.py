"""Reassign LAN from igb1 to igb2 (third port) to work around TX issue."""

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
    print("  Reassign LAN: igb1 -> igb2")
    print("=" * 55)
    print("  igb1 (port 2) has a TX hardware issue.")
    print("  Moving LAN to igb2 (port 3).")
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

    if "Enter an option:" not in resp:
        print("[!] Cannot reach menu.")
        ser.close()
        return

    # First let me check igb0 TX to confirm WAN port works
    print("[*] First checking if igb0 TX works (sanity check)...")
    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")

    out = run(ser, "netstat -I igb0 -b | grep Link")
    print(f"  igb0 (WAN): {out.strip()}")

    # Also quickly test igb2-igb5 TX capability
    print()
    print("[*] Testing which ports can transmit...")
    for i in range(6):
        # Bring up interface
        run(ser, f"ifconfig igb{i} up 2>/dev/null")
        run(ser, f"ifconfig igb{i} -txcsum -rxcsum -tso4 -tso6 -lro 2>/dev/null")
    time.sleep(2)

    # Get TX counters for all
    for i in range(6):
        out = run(ser, f"netstat -I igb{i} -b 2>/dev/null | grep Link")
        if out.strip():
            parts = out.strip().split()
            # Format: igb0 1500 <Link#1> MAC Ipkts Ierrs Idrop Ibytes Opkts Oerrs Obytes Coll
            if len(parts) >= 9:
                print(f"  igb{i}: IN={parts[4]} pkts, OUT={parts[8]} pkts")
            else:
                print(f"  igb{i}: {out.strip()}")
    print()

    # Exit shell
    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    # Use Option 1 to reassign interfaces
    print("=" * 55)
    print("  Reassigning interfaces: WAN=igb0, LAN=igb2")
    print("=" * 55)
    print()

    # Option 1: Assign interfaces
    print("[1] Sending option 1...")
    send(ser, "1\n", delay=1)
    resp = read_until(ser, r"VLANs be set up|set up now", timeout=20)
    for line in resp.splitlines():
        s = line.strip()
        if s and "igb" in s.lower():
            print(f"  {s}")
    print()

    # No VLANs
    print("[2] No VLANs (n)...")
    send(ser, "n\n", delay=1)
    resp = read_until(ser, r"Enter the WAN interface", timeout=15)
    print()

    # WAN = igb0
    print("[3] WAN = igb0...")
    send(ser, "igb0\n", delay=1)
    resp = read_until(ser, r"Enter the LAN interface", timeout=15)
    print()

    # LAN = igb2 (NOT igb1)
    print("[4] LAN = igb2 (third port)...")
    send(ser, "igb2\n", delay=1)
    resp = read_until(ser, r"Optional|proceed", timeout=15)
    print()

    # No optional
    if "Optional" in resp:
        print("[5] No optional interfaces...")
        send(ser, "\n", delay=1)
        resp = read_until(ser, r"proceed", timeout=15)

    # Confirm
    print("[6] Confirming...")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s or "WAN" in s or "LAN" in s:
            print(f"  {s}")

    send(ser, "y\n", delay=2)
    print("[*] Waiting for pfSense to apply (this takes a while)...")
    resp = read_until(ser, r"Enter an option:", timeout=120)
    print()

    # Show result
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")
    print()

    # Now set LAN IP on the new interface
    print("=" * 55)
    print("  Setting LAN IP on igb2")
    print("=" * 55)
    print()

    print("[1] Option 2: Set interface IP...")
    send(ser, "2\n", delay=1)
    resp = read_until(ser, r"number of the interface|Enter the number", timeout=15)

    # Select LAN (2)
    print("[2] Selecting LAN (2)...")
    send(ser, "2\n", delay=1)
    resp = read_until(ser, r"DHCP.*\(y/n\)", timeout=15)

    # Static IP
    print("[3] Static IP (n)...")
    send(ser, "n\n", delay=1)
    resp = read_until(ser, r"Enter the new.*IPv4 address", timeout=10)

    print("[4] IP = 192.168.1.254...")
    send(ser, "192.168.1.254\n", delay=1)
    resp = read_until(ser, r"subnet bit count", timeout=10)

    print("[5] Subnet = /24...")
    send(ser, "24\n", delay=1)
    resp = read_until(ser, r"gateway|ENTER.*for none", timeout=10)

    print("[6] No gateway...")
    send(ser, "\n", delay=1)
    resp = read_until(ser, r"IPv6.*DHCP|DHCP6", timeout=10)

    print("[7] No IPv6 (n)...")
    send(ser, "n\n", delay=1)
    resp = read_until(ser, r"IPv6 address|DHCP server|enable.*DHCP", timeout=10)

    if "ipv6 address" in resp.lower():
        send(ser, "\n", delay=1)
        resp = read_until(ser, r"DHCP server|enable.*DHCP", timeout=10)

    print("[8] Enable DHCP (y)...")
    send(ser, "y\n", delay=1)
    resp = read_until(ser, r"start address", timeout=10)

    print("[9] DHCP start = 192.168.1.100...")
    send(ser, "192.168.1.100\n", delay=1)
    resp = read_until(ser, r"end address", timeout=10)

    print("[10] DHCP end = 192.168.1.199...")
    send(ser, "192.168.1.199\n", delay=1)
    resp = read_until(ser, r"revert.*HTTP|webConfigurator|Enter an option:", timeout=15)

    if "revert" in resp.lower():
        print("[11] Keep HTTPS (n)...")
        send(ser, "n\n", delay=1)
        resp = read_until(ser, r"Press.*ENTER|Enter an option:", timeout=30)

    if "Press" in resp and "ENTER" in resp:
        send(ser, "\r\n", delay=2)
        resp = read_until(ser, r"Enter an option:", timeout=30)

    # Enter shell to disable offloads on igb2 and test TX
    print()
    print("[*] Testing igb2 TX...")
    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")

    # Disable offloads on igb2
    run(ser, "ifconfig igb2 -txcsum -rxcsum -tso4 -tso6 -lro")
    time.sleep(1)

    # Get counters
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  igb2 counters: {out.strip()}")

    # Ping test
    out = run(ser, "ping -c 2 -t 1 192.168.1.255", timeout=8)
    for line in out.splitlines():
        if "transmitted" in line or "received" in line:
            print(f"  {line.strip()}")

    # Get counters after
    out = run(ser, "netstat -I igb2 -b | grep Link")
    print(f"  igb2 after:    {out.strip()}")

    # Check status
    out = run(ser, "ifconfig igb2 | grep status")
    print(f"  igb2 status:   {out.strip()}")
    print()

    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    print("=" * 55)
    print("  RESULT")
    print("=" * 55)
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("  IMPORTANT: Move your Ethernet cable from")
    print("  port 2 to port 3 on the Sophos SG 230!")
    print()
    print("  Port layout (looking at the back):")
    print("    Port 1 = igb0 (WAN - connected to router)")
    print("    Port 2 = igb1 (BROKEN - do not use)")
    print("    Port 3 = igb2 (LAN - plug your PC here)")
    print()
    print("  Then: ipconfig /release && ipconfig /renew")
    print("  Then: https://192.168.1.254")


if __name__ == "__main__":
    main()
