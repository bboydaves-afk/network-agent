"""Fix pfSense interfaces: restore WAN to DHCP, assign LAN, configure LAN IP.

The previous script accidentally set WAN to static 192.168.1.254/24.
This script:
  1. Restores WAN (igb0) to DHCP
  2. Assigns igb1 as LAN using option 1
  3. Sets LAN IP to 192.168.1.254/24 with DHCP server
"""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

LAN_IP = "192.168.1.254"
LAN_SUBNET = "24"
LAN_DHCP_START = "192.168.1.100"
LAN_DHCP_END = "192.168.1.199"


def clean(text):
    return ANSI.sub("", text)


def read_until(ser, pattern, timeout=30):
    """Read serial until pattern is found or timeout."""
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


def read_all(ser, timeout=5):
    """Read everything available."""
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            buf += chunk
            end = time.time() + 2
        else:
            time.sleep(0.2)
    return clean(buf)


def send(ser, text):
    """Send text over serial."""
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(0.5)


def log(resp):
    """Print response lines."""
    for line in resp.strip().splitlines():
        safe = line.encode("ascii", errors="replace").decode("ascii").strip()
        if safe:
            print(f"  | {safe}")


def wait_for_menu(ser, timeout=30):
    """Wait for pfSense console menu."""
    resp = read_until(ser, r"Enter an option:", timeout=timeout)
    return resp


def main():
    print("=" * 55)
    print("  pfSense Interface Fix")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Get to the menu
    print("[*] Connecting to pfSense console...")
    send(ser, "\r\n")
    resp = wait_for_menu(ser, timeout=10)
    if "Enter an option:" not in resp:
        # Might need to press Enter for "Press <ENTER> to continue"
        send(ser, "\r\n")
        resp = wait_for_menu(ser, timeout=10)

    if "Enter an option:" not in resp:
        print("[!] Cannot reach pfSense menu.")
        print(f"[!] Got: {repr(resp[:300])}")
        ser.close()
        return

    print("[*] Console menu active.")
    log(resp)
    print()

    # =====================================================
    # STEP 1: Restore WAN to DHCP
    # =====================================================
    print("=" * 55)
    print("  STEP 1: Restore WAN to DHCP")
    print("=" * 55)
    print()

    send(ser, "2\n")
    # Since there's only WAN, it goes straight to WAN config
    resp = read_until(ser, r"DHCP.*\(y/n\)|number of the interface", timeout=15)
    log(resp)

    if "number of the interface" in resp.lower():
        # Multiple interfaces - select WAN (1)
        print("[*] Selecting WAN (option 1)...")
        send(ser, "1\n")
        resp = read_until(ser, r"DHCP.*\(y/n\)", timeout=15)
        log(resp)

    # Set WAN to DHCP
    print("[*] Setting WAN to DHCP...")
    send(ser, "y\n")
    # IPv6 DHCP?
    resp = read_until(ser, r"IPv6.*DHCP|DHCP6.*\(y/n\)", timeout=10)
    log(resp)

    # Also set IPv6 to DHCP
    print("[*] Setting WAN IPv6 to DHCP6...")
    send(ser, "y\n")

    # "Do you want to revert to HTTP?" or apply changes
    resp = read_until(ser, r"revert.*HTTP|webConfigurator|Press.*ENTER|Enter an option:", timeout=20)
    log(resp)

    if "revert" in resp.lower() or "http" in resp.lower():
        print("[*] Keeping HTTPS...")
        send(ser, "n\n")
        resp = read_until(ser, r"Press.*ENTER|Enter an option:", timeout=30)
        log(resp)

    if "Press" in resp and "ENTER" in resp:
        send(ser, "\r\n")
        resp = wait_for_menu(ser, timeout=30)

    print()
    print("[*] WAN restored to DHCP.")
    log(resp)
    print()

    # =====================================================
    # STEP 2: Assign igb1 as LAN
    # =====================================================
    print("=" * 55)
    print("  STEP 2: Assign igb1 as LAN interface")
    print("=" * 55)
    print()

    # Make sure we're at the menu
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = wait_for_menu(ser, timeout=15)

    send(ser, "1\n")
    # pfSense asks: Should VLANs be set up first? (y/n)
    resp = read_until(ser, r"VLAN.*\(y/n\)|Do you want to set up VLANs|WAN interface name", timeout=15)
    log(resp)

    if "vlan" in resp.lower() and "y/n" in resp.lower():
        print("[*] Declining VLAN setup for now...")
        send(ser, "n\n")
        resp = read_until(ser, r"WAN interface|Enter the WAN", timeout=15)
        log(resp)

    # Enter WAN interface name
    if "wan" in resp.lower() and "interface" in resp.lower():
        print("[*] Assigning igb0 as WAN...")
        send(ser, "igb0\n")
        resp = read_until(ser, r"LAN interface|Enter the LAN", timeout=15)
        log(resp)

    # Enter LAN interface name
    if "lan" in resp.lower() and "interface" in resp.lower():
        print("[*] Assigning igb1 as LAN...")
        send(ser, "igb1\n")
        resp = read_until(ser, r"Optional.*interface|Enter the Optional|Do you want to proceed|proceed.*\(y/n\)", timeout=15)
        log(resp)

    # Optional interface - press Enter for none
    if "optional" in resp.lower() and "interface" in resp.lower():
        print("[*] No optional interfaces...")
        send(ser, "\n")
        resp = read_until(ser, r"proceed.*\(y/n\)|Do you want to proceed", timeout=15)
        log(resp)

    # Confirm
    if "proceed" in resp.lower() or "y/n" in resp.lower():
        print("[*] Confirming interface assignment...")
        send(ser, "y\n")
        # This takes a while as pfSense reloads
        print("[*] Waiting for pfSense to apply interface changes...")
        resp = read_until(ser, r"Enter an option:", timeout=120)
        log(resp)

    # Check if LAN appears
    if "LAN" in resp:
        print()
        print("[*] LAN interface assigned successfully!")
    else:
        # Wait more and try to get updated menu
        send(ser, "\r\n")
        resp = wait_for_menu(ser, timeout=30)
        log(resp)

    print()

    # =====================================================
    # STEP 3: Configure LAN IP address
    # =====================================================
    print("=" * 55)
    print(f"  STEP 3: Set LAN IP to {LAN_IP}/{LAN_SUBNET}")
    print("=" * 55)
    print()

    # Make sure we're at the menu
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = wait_for_menu(ser, timeout=15)

    send(ser, "2\n")
    # Now with both WAN and LAN, it should show a list
    resp = read_until(ser, r"number of the interface|Enter the number|DHCP.*\(y/n\)", timeout=15)
    log(resp)

    if "number of the interface" in resp.lower() or "enter the number" in resp.lower():
        # Select LAN (should be 2)
        print("[*] Selecting LAN (option 2)...")
        send(ser, "2\n")
        resp = read_until(ser, r"DHCP.*\(y/n\)", timeout=15)
        log(resp)

    # Configure IPv4 via DHCP? No - static
    print(f"[*] Setting static IP: {LAN_IP}/{LAN_SUBNET}...")
    send(ser, "n\n")
    resp = read_until(ser, r"Enter the new.*IPv4 address", timeout=10)
    log(resp)

    # Enter IP
    send(ser, f"{LAN_IP}\n")
    resp = read_until(ser, r"subnet bit count|subnet mask", timeout=10)
    log(resp)

    # Enter subnet
    send(ser, f"{LAN_SUBNET}\n")
    resp = read_until(ser, r"upstream gateway|gateway address|ENTER.*for none", timeout=10)
    log(resp)

    # No upstream gateway for LAN
    print("[*] No upstream gateway for LAN...")
    send(ser, "\n")
    resp = read_until(ser, r"IPv6.*DHCP|DHCP6.*\(y/n\)|configure.*IPv6", timeout=10)
    log(resp)

    # IPv6 - decline
    print("[*] No IPv6 on LAN...")
    send(ser, "n\n")
    resp = read_until(ser, r"Enter the new.*IPv6|DHCP server|enable.*DHCP", timeout=10)
    log(resp)

    # If it asks for IPv6 address, press Enter
    if "ipv6 address" in resp.lower():
        send(ser, "\n")
        resp = read_until(ser, r"DHCP server|enable.*DHCP", timeout=10)
        log(resp)

    # Enable DHCP server
    print(f"[*] Enabling DHCP ({LAN_DHCP_START} - {LAN_DHCP_END})...")
    send(ser, "y\n")
    resp = read_until(ser, r"start address|start of.*range", timeout=10)
    log(resp)

    # Start address
    send(ser, f"{LAN_DHCP_START}\n")
    resp = read_until(ser, r"end address|end of.*range", timeout=10)
    log(resp)

    # End address
    send(ser, f"{LAN_DHCP_END}\n")
    resp = read_until(ser, r"revert.*HTTP|webConfigurator|Press.*ENTER|Enter an option:", timeout=15)
    log(resp)

    # Keep HTTPS
    if "revert" in resp.lower() or "http" in resp.lower():
        print("[*] Keeping HTTPS...")
        send(ser, "n\n")
        resp = read_until(ser, r"Press.*ENTER|Enter an option:", timeout=30)
        log(resp)

    if "Press" in resp and "ENTER" in resp:
        send(ser, "\r\n")
        resp = wait_for_menu(ser, timeout=30)

    # Final verification
    print()
    print("[*] Getting final state...")
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = wait_for_menu(ser, timeout=15)

    print()
    print("=" * 55)
    print("  FINAL RESULT")
    print("=" * 55)
    log(resp)

    ser.close()

    if LAN_IP in resp and "LAN" in resp:
        print()
        print(f"  SUCCESS!")
        print(f"  WAN (igb0) = DHCP from router")
        print(f"  LAN (igb1) = {LAN_IP}/{LAN_SUBNET}")
        print()
        print(f"  WebGUI: https://{LAN_IP}")
        print(f"  Login:  admin / pfsense")
    else:
        print()
        print("  Check output above for any issues.")


if __name__ == "__main__":
    main()
