"""Assign switch ports to VLANs on Aruba 2930F via serial console."""

import serial
import time
import re
import sys
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
PROMPT_RE = re.compile(r"[\w\-\.]+(?:\([^\)]+\))?\s*[#>]\s*$")
ANSI_RE = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")


def clean(text):
    return ANSI_RE.sub("", text)


def read_until_prompt(ser, timeout=20):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        w = ser.in_waiting
        if w > 0:
            buf += ser.read(w).decode("utf-8", errors="replace")
            c = clean(buf)
            if PROMPT_RE.search(c):
                return c
        else:
            time.sleep(0.2)
    return clean(buf)


def read_until_pattern(ser, pattern, timeout=15):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        w = ser.in_waiting
        if w > 0:
            buf += ser.read(w).decode("utf-8", errors="replace")
            c = clean(buf)
            if re.search(pattern, c, re.IGNORECASE):
                return c
        else:
            time.sleep(0.2)
    return clean(buf)


def cmd(ser, command, wait=1.0, timeout=20):
    ser.write((command + "\n").encode("utf-8"))
    ser.flush()
    time.sleep(wait)
    return read_until_prompt(ser, timeout=timeout)


def main():
    print("=" * 55)
    print("  Aruba 2930F - VLAN Port Assignment")
    print("=" * 55)
    print()

    # Open serial
    print("[*] Opening COM3 at 115200 baud...")
    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Wake console and detect state
    ser.write(b"\r\n")
    time.sleep(3)
    initial = ""
    if ser.in_waiting:
        initial = clean(ser.read(ser.in_waiting).decode("utf-8", errors="replace"))

    print(f"[*] Console response: {repr(initial[:150])}")

    # Check if we need to authenticate
    needs_auth = False
    if "assword" in initial or "sername" in initial or "User Name" in initial:
        needs_auth = True
    else:
        # Send another Enter and check
        ser.write(b"\r\n")
        time.sleep(2)
        resp = read_until_pattern(ser, r"[#>]|assword|sername|User Name", timeout=10)
        if "assword" in resp or "sername" in resp or "User Name" in resp:
            needs_auth = True
            initial = resp
        elif PROMPT_RE.search(resp):
            print("[*] Already at CLI prompt.")
        else:
            print(f"[*] Got: {repr(resp[:150])}")

    if needs_auth:
        print("[*] Authenticating as manager...")
        if "sername" in initial or "User Name" in initial:
            ser.write(b"manager\n")
            ser.flush()
            time.sleep(2)
            read_until_pattern(ser, r"assword", timeout=10)

        ser.write(b"Welcome01!\n")
        ser.flush()
        time.sleep(3)
        login_resp = read_until_prompt(ser, timeout=15)
        if PROMPT_RE.search(login_resp):
            print("[*] Logged in successfully.")
        else:
            # Try one more Enter
            ser.write(b"\r\n")
            time.sleep(2)
            read_until_prompt(ser, timeout=10)
            print("[*] Login completed.")

    # Disable paging
    cmd(ser, "no page", wait=1)

    # Enter config
    print("[*] Entering config mode...")
    cmd(ser, "configure terminal", wait=1)

    # --- Port assignments ---
    print()
    print("[1/4] Ports 1-12 -> VLAN 10 (DATA)...")
    cmd(ser, "vlan 10", wait=0.5)
    resp = cmd(ser, "untagged 1-12", wait=2)
    cmd(ser, "exit", wait=0.5)
    print("       Done.")

    print("[2/4] Ports 13-20 -> VLAN 20 (VOICE)...")
    cmd(ser, "vlan 20", wait=0.5)
    resp = cmd(ser, "untagged 13-20", wait=2)
    cmd(ser, "exit", wait=0.5)
    print("       Done.")

    print("[3/4] Ports 21-22 -> VLAN 30 (GUEST)...")
    cmd(ser, "vlan 30", wait=0.5)
    resp = cmd(ser, "untagged 21-22", wait=2)
    cmd(ser, "exit", wait=0.5)
    print("       Done.")

    print("[4/4] Ports 23-28 -> Trunk (all VLANs tagged)...")
    for vid in ["10", "20", "30", "50", "100"]:
        cmd(ser, "vlan %s" % vid, wait=0.5)
        cmd(ser, "tagged 23-28", wait=2)
        cmd(ser, "exit", wait=0.5)
    print("       VLANs 10,20,30,50,100 tagged on ports 23-28.")

    # Save
    print()
    print("[*] Saving configuration...")
    cmd(ser, "exit", wait=1)
    resp = cmd(ser, "write memory", wait=5, timeout=30)
    if "y/n" in resp.lower():
        cmd(ser, "y", wait=5)
    print("[*] Configuration saved to flash.")

    # Verify
    print()
    print("=" * 55)
    print("  VERIFICATION")
    print("=" * 55)

    print()
    print("--- VLAN Summary ---")
    out = cmd(ser, "show vlans", wait=3, timeout=15)
    print(out)

    print()
    print("--- VLAN 10 (DATA) Ports ---")
    out = cmd(ser, "show vlans 10", wait=3, timeout=15)
    print(out)

    print()
    print("--- VLAN 20 (VOICE) Ports ---")
    out = cmd(ser, "show vlans 20", wait=3, timeout=15)
    print(out)

    print()
    print("--- VLAN 30 (GUEST) Ports ---")
    out = cmd(ser, "show vlans 30", wait=3, timeout=15)
    print(out)

    ser.close()
    print()
    print("[*] Done. Serial connection closed.")


if __name__ == "__main__":
    main()
