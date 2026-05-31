"""Configure pfSense LAN interface (igb1) via serial console.

Sets LAN IP to 192.168.1.254/24 to match the Aruba 2930F switch
default gateway configuration. Enables DHCP on 192.168.1.100-199.

Usage:
  1. Ensure pfSense is booted and showing the console menu on COM3
  2. Run this script
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


def main():
    print("=" * 55)
    print("  pfSense LAN Interface Configuration")
    print("=" * 55)
    print()
    print(f"  Target: LAN (igb1) = {LAN_IP}/{LAN_SUBNET}")
    print(f"  DHCP:   {LAN_DHCP_START} - {LAN_DHCP_END}")
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Wake and confirm console menu
    print("[*] Checking pfSense console...")
    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=10)

    if "Enter an option:" not in resp:
        print("[!] Console menu not detected. Retrying...")
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    if "Enter an option:" not in resp:
        print("[!] FAILED: pfSense console menu not found.")
        print(f"[!] Got: {repr(resp[:300])}")
        ser.close()
        return

    print("[*] Console menu active.")
    log(resp)
    print()

    # Step 1: Select option 2 (Set interface IP address)
    print("[1/6] Selecting 'Set interface(s) IP address'...")
    send(ser, "2\n")
    # Wait for interface list / "Enter the number"
    resp = read_until(ser, r"Enter the number|which interface", timeout=15)
    log(resp)
    print()

    # Step 2: Select LAN (interface 2)
    print("[2/6] Selecting LAN interface...")
    send(ser, "2\n")
    # Wait for DHCP question
    resp = read_until(ser, r"DHCP.*\(y/n\)|IPv4 address.*DHCP", timeout=15)
    log(resp)
    print()

    # Step 3: Decline DHCP, set static IP
    print(f"[3/6] Setting static IPv4: {LAN_IP}/{LAN_SUBNET}...")
    # "Configure IPv4 address LAN interface via DHCP? (y/n)"
    send(ser, "n\n")
    # Wait for "Enter the new LAN IPv4 address"
    resp = read_until(ser, r"Enter the new.*IPv4 address", timeout=10)
    log(resp)

    # Enter IP address
    send(ser, f"{LAN_IP}\n")
    # Wait for subnet bit count prompt
    resp = read_until(ser, r"subnet bit count|subnet mask", timeout=10)
    log(resp)

    # Enter subnet
    send(ser, f"{LAN_SUBNET}\n")
    # Wait for upstream gateway prompt
    resp = read_until(ser, r"upstream gateway|gateway address|ENTER.*for none|IPv6", timeout=10)
    log(resp)

    # If it asks for upstream gateway, press Enter (none for LAN)
    if "gateway" in resp.lower() and "ipv6" not in resp.lower():
        print("  (No upstream gateway for LAN)")
        send(ser, "\n")
        resp = read_until(ser, r"IPv6|DHCP6|configure ipv6", timeout=10)
        log(resp)

    print()

    # Step 4: IPv6 - decline
    print("[4/6] Declining IPv6...")
    if "ipv6" in resp.lower() or "dhcp6" in resp.lower():
        send(ser, "n\n")
        # Wait for IPv6 address prompt or DHCP server prompt
        resp = read_until(ser, r"Enter the new.*IPv6|DHCP server|enable.*DHCP", timeout=10)
        log(resp)

        # If it asks for IPv6 address, press Enter for none
        if "ipv6 address" in resp.lower() and "enter" in resp.lower():
            send(ser, "\n")
            resp = read_until(ser, r"DHCP server|enable.*DHCP", timeout=10)
            log(resp)
    print()

    # Step 5: Enable DHCP server
    print(f"[5/6] Enabling DHCP server ({LAN_DHCP_START} - {LAN_DHCP_END})...")
    if "dhcp" in resp.lower():
        send(ser, "y\n")
        # Wait for start address prompt
        resp = read_until(ser, r"start address|start of.*range", timeout=10)
        log(resp)

        # Enter start address
        send(ser, f"{LAN_DHCP_START}\n")
        # Wait for end address prompt
        resp = read_until(ser, r"end address|end of.*range", timeout=10)
        log(resp)

        # Enter end address
        send(ser, f"{LAN_DHCP_END}\n")
        # Wait for HTTP revert question or completion
        resp = read_until(ser, r"revert.*HTTP|webConfigurator|Enter an option:", timeout=15)
        log(resp)
    print()

    # Step 6: Keep HTTPS
    print("[6/6] Keeping HTTPS for WebGUI...")
    if "revert" in resp.lower() or "http" in resp.lower():
        send(ser, "n\n")
        # Wait for pfSense to apply changes and return to menu
        resp = read_until(ser, r"Enter an option:", timeout=30)
        log(resp)

    # Final: confirm the menu shows LAN with our IP
    print()
    print("[*] Waiting for configuration to apply...")
    time.sleep(3)
    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=15)

    print()
    print("=" * 55)
    print("  RESULT")
    print("=" * 55)
    log(resp)

    # Check if our IP appears
    if LAN_IP in resp:
        print()
        print(f"  SUCCESS: LAN is configured as {LAN_IP}/{LAN_SUBNET}")
    else:
        print()
        print("  WARNING: Could not confirm LAN IP in menu output.")
        print("  Check the pfSense console manually.")

    ser.close()
    print()
    print("=" * 55)
    print("  Next Steps")
    print("=" * 55)
    print()
    print(f"  1. Connect Aruba switch trunk port (23-28) to igb1")
    print(f"     on the Sophos SG 230 (second Ethernet port)")
    print()
    print(f"  2. Browse to https://{LAN_IP} for the WebGUI")
    print(f"     Username: admin")
    print(f"     Password: pfsense  (CHANGE THIS!)")
    print()
    print(f"  3. Configure VLANs on pfSense to match switch:")
    print(f"     VLAN 10 (DATA)  - 10.10.10.254/24")
    print(f"     VLAN 20 (VOICE) - 10.20.20.254/24")
    print(f"     VLAN 30 (GUEST) - 10.30.30.254/24")
    print(f"     VLAN 50 (MGMT)  - 192.168.1.254/24 (already LAN)")
    print(f"     VLAN 100 (MGMT) - 10.100.100.254/24")


if __name__ == "__main__":
    main()
