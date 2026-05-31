"""Fix pfSense: assign igb1 as LAN, then set LAN IP.

Step-by-step with careful pattern matching for pfSense 2.8.1 console.
"""

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
    print(f"  [TIMEOUT waiting for: {pattern}]")
    return clean(buf)


def send(ser, text, delay=0.5):
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(delay)


def show(resp, last_n=5):
    lines = resp.strip().splitlines()
    for line in lines[-last_n:]:
        safe = line.encode("ascii", errors="replace").decode("ascii").strip()
        if safe and "resizewin" not in safe:
            print(f"    {safe}")


def main():
    print("=" * 55)
    print("  pfSense LAN Setup v3")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Get to menu
    print("[*] Connecting...")
    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=10)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    if "Enter an option:" not in resp:
        print("[!] Cannot reach menu. Aborting.")
        ser.close()
        return

    # Check current state
    has_lan = "LAN" in resp and "lan" in resp.lower() and "->" in resp
    wan_dhcp = "DHCP" in resp

    for line in resp.strip().splitlines():
        s = line.encode("ascii", errors="replace").decode("ascii").strip()
        if "->" in s:
            print(f"  {s}")

    if has_lan:
        print()
        print("[*] LAN already assigned! Skipping to IP config.")
    else:
        print()
        print("[*] LAN not assigned. Using Option 1...")

        # ============================
        # OPTION 1: Assign Interfaces
        # ============================
        print()
        print("--- Assign Interfaces (Option 1) ---")
        print()

        # Send option 1
        print("[1] Sending option 1...")
        send(ser, "1\n", delay=1)

        # Wait for VLAN question: "Should VLANs be set up now [y|n]?"
        resp = read_until(ser, r"VLANs be set up|set up now", timeout=20)
        show(resp)
        print()

        # Answer: no VLANs
        print("[2] Declining VLANs (n)...")
        send(ser, "n\n", delay=1)

        # Wait for: "Enter the WAN interface name"
        resp = read_until(ser, r"Enter the WAN interface", timeout=15)
        show(resp)
        print()

        # Answer: igb0 for WAN
        print("[3] Setting WAN = igb0...")
        send(ser, "igb0\n", delay=1)

        # Wait for: "Enter the LAN interface name"
        resp = read_until(ser, r"Enter the LAN interface|Enter the Optional", timeout=15)
        show(resp)
        print()

        if "LAN interface" in resp:
            # Answer: igb1 for LAN
            print("[4] Setting LAN = igb1...")
            send(ser, "igb1\n", delay=1)

            # Wait for: "Enter the Optional 1 interface name"
            resp = read_until(ser, r"Optional|proceed", timeout=15)
            show(resp)
            print()
        else:
            print("[!] Did not get LAN prompt. Got Optional instead.")
            show(resp)

        if "Optional" in resp:
            # No optional interfaces
            print("[5] No optional interfaces (Enter)...")
            send(ser, "\n", delay=1)

            # Wait for confirmation
            resp = read_until(ser, r"proceed|y/n|\[y\|n\]", timeout=15)
            show(resp)
            print()

        # Confirm
        if "proceed" in resp.lower() or "y" in resp.lower():
            print("[6] Confirming (y)...")
            send(ser, "y\n", delay=2)

            # Wait for pfSense to reconfigure - this can take 30-60 seconds
            print("[*] Waiting for pfSense to apply changes...")
            resp = read_until(ser, r"Enter an option:", timeout=120)
            show(resp, 15)
            print()

            if "LAN" in resp:
                print("[*] LAN interface assigned!")
            else:
                print("[!] LAN may not have been assigned. Checking...")
        else:
            print("[!] No confirmation prompt found.")
            show(resp)

    # ============================
    # OPTION 2: Set LAN IP
    # ============================
    # Make sure we're at the menu
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=15)

    # Check if LAN is now listed
    if "LAN" not in resp:
        print()
        print("[!] LAN still not in menu. Current state:")
        show(resp, 20)
        print()
        print("[!] The interface assignment may have failed.")
        print("[!] Try manually: from the pfSense menu, select option 1")
        print("[!]   VLANs: n, WAN: igb0, LAN: igb1, Optional: (enter), proceed: y")
        ser.close()
        return

    print()
    print("--- Set LAN IP (Option 2) ---")
    print()

    print("[1] Sending option 2...")
    send(ser, "2\n", delay=1)

    # Should show interface list now
    resp = read_until(ser, r"Enter the number|number of the interface", timeout=15)
    show(resp)
    print()

    # Select LAN (2)
    print("[2] Selecting LAN (2)...")
    send(ser, "2\n", delay=1)

    # "Configure IPv4 address LAN interface via DHCP? (y/n)"
    resp = read_until(ser, r"DHCP.*\(y/n\)", timeout=15)
    show(resp)
    print()

    # Static IP (not DHCP)
    print("[3] Static IP (n)...")
    send(ser, "n\n", delay=1)

    # "Enter the new LAN IPv4 address"
    resp = read_until(ser, r"Enter the new.*IPv4 address", timeout=10)
    show(resp)

    print(f"[4] IP = 192.168.1.254...")
    send(ser, "192.168.1.254\n", delay=1)

    # "Enter the new LAN IPv4 subnet bit count"
    resp = read_until(ser, r"subnet bit count", timeout=10)
    show(resp)

    print("[5] Subnet = /24...")
    send(ser, "24\n", delay=1)

    # "For a LAN, press <ENTER> for none" (gateway)
    resp = read_until(ser, r"gateway|ENTER.*for none", timeout=10)
    show(resp)

    print("[6] No gateway (Enter)...")
    send(ser, "\n", delay=1)

    # "Configure IPv6 address LAN interface via DHCP6? (y/n)"
    resp = read_until(ser, r"IPv6.*DHCP|DHCP6", timeout=10)
    show(resp)

    print("[7] No IPv6 DHCP (n)...")
    send(ser, "n\n", delay=1)

    # "Enter the new LAN IPv6 address. Press <ENTER> for none"
    resp = read_until(ser, r"IPv6 address|DHCP server|enable.*DHCP", timeout=10)
    show(resp)

    if "ipv6 address" in resp.lower():
        print("[8] No IPv6 (Enter)...")
        send(ser, "\n", delay=1)
        resp = read_until(ser, r"DHCP server|enable.*DHCP", timeout=10)
        show(resp)

    # "Do you want to enable the DHCP server on LAN? (y/n)"
    print("[9] Enable DHCP (y)...")
    send(ser, "y\n", delay=1)

    resp = read_until(ser, r"start address", timeout=10)
    show(resp)

    print("[10] DHCP start = 192.168.1.100...")
    send(ser, "192.168.1.100\n", delay=1)

    resp = read_until(ser, r"end address", timeout=10)
    show(resp)

    print("[11] DHCP end = 192.168.1.199...")
    send(ser, "192.168.1.199\n", delay=1)

    # "Do you want to revert to HTTP?"
    resp = read_until(ser, r"revert.*HTTP|webConfigurator|Press.*ENTER|Enter an option:", timeout=15)
    show(resp)

    if "revert" in resp.lower():
        print("[12] Keep HTTPS (n)...")
        send(ser, "n\n", delay=1)
        resp = read_until(ser, r"Press.*ENTER|Enter an option:", timeout=30)
        show(resp)

    if "Press" in resp and "ENTER" in resp:
        send(ser, "\r\n", delay=2)
        resp = read_until(ser, r"Enter an option:", timeout=30)

    # Final state
    print()
    print("=" * 55)
    print("  RESULT")
    print("=" * 55)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=15)

    for line in resp.strip().splitlines():
        s = line.encode("ascii", errors="replace").decode("ascii").strip()
        if s and "resizewin" not in s:
            if "->" in s or "Welcome" in s or "pfSense" in s:
                print(f"  {s}")

    ser.close()
    print()
    print("  WebGUI: https://192.168.1.254")
    print("  Login:  admin / pfsense")
    print("  IMPORTANT: Change the default password!")


if __name__ == "__main__":
    main()
