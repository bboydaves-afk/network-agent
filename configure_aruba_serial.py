"""
Configure Aruba ProCurve (AOS-Switch) via serial console on COM3.

Initial setup: hostname, VLANs, inter-VLAN routing, OSPF, SSH, NTP.
"""

import serial
import time
import re
import sys

SERIAL_PORT = "COM3"
BAUDRATE = 115200
TIMEOUT = 5

# ProCurve prompt pattern: hostname# or hostname(config)#
PROMPT_RE = re.compile(r"[\w\-\.]+(?:\([^\)]+\))?\s*[#>]\s*$")


def read_until_prompt(ser, timeout=15):
    """Read serial output until a CLI prompt is detected."""
    buffer = ""
    end_time = time.time() + timeout
    while time.time() < end_time:
        waiting = ser.in_waiting
        if waiting > 0:
            chunk = ser.read(waiting).decode("utf-8", errors="replace")
            buffer += chunk
            # Strip ANSI escapes
            clean = re.sub(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00", "", buffer)
            if PROMPT_RE.search(clean):
                return clean
        else:
            time.sleep(0.2)
    return buffer


def send_line(ser, command, wait=1.5, timeout=15):
    """Send a command line and wait for prompt."""
    ser.write((command + "\n").encode("utf-8"))
    ser.flush()
    time.sleep(wait)
    output = read_until_prompt(ser, timeout=timeout)
    return output


def main():
    print("=" * 60)
    print("  Aruba ProCurve Serial Configuration Script")
    print(f"  Port: {SERIAL_PORT} @ {BAUDRATE} baud")
    print("=" * 60)
    print()

    # --- Connect ---
    print("[1/8] Opening serial connection...")
    try:
        ser = serial.Serial(
            port=SERIAL_PORT,
            baudrate=BAUDRATE,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=TIMEOUT,
        )
    except serial.SerialException as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    # Wake console
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    ser.write(b"\r\n")
    time.sleep(3)
    initial = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
    print(f"  Connected. Initial output:\n  {initial.strip()[:200]}")

    # Handle "Press any key" banners
    if "press any key" in initial.lower():
        ser.write(b" ")
        time.sleep(2)
        ser.read(ser.in_waiting)

    # If we see a login prompt, try blank credentials (fresh switch)
    if "ogin:" in initial or "sername:" in initial or "User Name:" in initial:
        print("  Login prompt detected -- sending blank credentials...")
        ser.write(b"\n")
        time.sleep(1)
        resp = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
        if "assword:" in resp:
            ser.write(b"\n")
            time.sleep(2)
            ser.read(ser.in_waiting)

    # Send an empty line to get a prompt
    output = send_line(ser, "", wait=2)
    print(f"  Prompt: {output.strip().splitlines()[-1] if output.strip() else '(detecting...)'}")

    # --- Enter config mode ---
    print("\n[2/8] Setting hostname and admin password...")
    send_line(ser, "configure terminal", wait=1)

    config_basic = [
        'hostname "Main-Core-Kingdom"',
        'password manager user-name manager plaintext "Welcome01!"',
    ]
    for cmd in config_basic:
        resp = send_line(ser, cmd)
        print(f"  {cmd}")

    # --- Create VLANs ---
    print("\n[3/8] Creating VLANs...")
    vlan_commands = [
        # VLAN 10 - DATA
        "vlan 10", 'name "DATA"', "exit",
        # VLAN 20 - VOICE
        "vlan 20", 'name "VOICE"', "exit",
        # VLAN 30 - GUEST
        "vlan 30", 'name "GUEST"', "exit",
        # VLAN 50 - MGMT
        "vlan 50", 'name "MGMT"', "exit",
        # VLAN 100 - MANAGEMENT
        "vlan 100", 'name "MANAGEMENT"', "exit",
    ]
    for cmd in vlan_commands:
        send_line(ser, cmd, wait=0.5)
    print("  VLAN 10 (DATA)")
    print("  VLAN 20 (VOICE)")
    print("  VLAN 30 (GUEST)")
    print("  VLAN 50 (MGMT)")
    print("  VLAN 100 (MANAGEMENT)")

    # --- Enable IP routing and assign VLAN IPs ---
    print("\n[4/8] Enabling IP routing and assigning VLAN IPs...")
    send_line(ser, "ip routing", wait=1)
    print("  ip routing enabled")

    vlan_ips = [
        ("10", "10.10.10.1 255.255.255.0"),
        ("20", "10.20.20.1 255.255.255.0"),
        ("30", "10.30.30.1 255.255.255.0"),
        ("50", "192.168.1.1 255.255.255.0"),
        ("100", "10.100.100.1 255.255.255.0"),
    ]
    for vlan_id, ip_mask in vlan_ips:
        send_line(ser, f"vlan {vlan_id}", wait=0.5)
        send_line(ser, f"ip address {ip_mask}", wait=0.5)
        send_line(ser, "exit", wait=0.5)
        print(f"  VLAN {vlan_id} -> {ip_mask.split()[0]}")

    # --- Default route ---
    print("\n[5/8] Setting default route...")
    send_line(ser, "ip route 0.0.0.0 0.0.0.0 192.168.1.254", wait=1)
    print("  Default gateway: 192.168.1.254")

    # --- OSPF ---
    print("\n[6/8] Configuring OSPF Area 0 on all VLANs...")
    ospf_commands = [
        "router ospf",
        "area backbone",
        "redistribute connected",
        "enable",
        "exit",
    ]
    for cmd in ospf_commands:
        send_line(ser, cmd, wait=0.5)
    print("  OSPF process enabled, redistribute connected")

    # Enable OSPF on each VLAN interface
    for vlan_id, _ in vlan_ips:
        send_line(ser, f"vlan {vlan_id}", wait=0.5)
        send_line(ser, "ip ospf area backbone", wait=0.5)
        send_line(ser, "exit", wait=0.5)
        print(f"  VLAN {vlan_id} -> OSPF area backbone")

    # --- Security: SSH, disable telnet, spanning-tree ---
    print("\n[7/8] Enabling SSH, disabling telnet, configuring STP & NTP...")
    security_commands = [
        "crypto key generate ssh rsa",
        "ip ssh",
        "no telnet-server",
        "spanning-tree",
        "spanning-tree mode rstp",
        "timesync ntp",
        "ntp unicast",
        "ntp server 216.239.35.0",
        "ntp enable",
        "console idle-timeout 300",
    ]
    for cmd in security_commands:
        resp = send_line(ser, cmd, wait=1)
        print(f"  {cmd}")
        # SSH key generation may prompt -- handle "y/n"
        if "continue" in resp.lower() or "y/n" in resp.lower():
            send_line(ser, "y", wait=3)

    # --- Exit config and save ---
    print("\n[8/8] Saving configuration...")
    send_line(ser, "exit", wait=1)  # exit config mode
    resp = send_line(ser, "write memory", wait=5, timeout=30)
    # ProCurve may ask for confirmation
    if "y/n" in resp.lower() or "continue" in resp.lower():
        send_line(ser, "y", wait=5)
    print("  Configuration saved to flash.")

    # --- Verify ---
    print("\n" + "=" * 60)
    print("  Configuration Complete!")
    print("=" * 60)
    print("\nVerifying... running 'show vlans':")
    verify = send_line(ser, "show vlans", wait=3, timeout=15)
    print(verify)

    print("\nVerifying... running 'show ip route':")
    verify = send_line(ser, "show ip route", wait=3, timeout=15)
    print(verify)

    print("\nVerifying... running 'show ip ospf':")
    verify = send_line(ser, "show ip ospf", wait=3, timeout=15)
    print(verify)

    ser.close()
    print("\nSerial connection closed. Switch is configured.")


if __name__ == "__main__":
    main()
