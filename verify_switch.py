"""Verify and save switch config - handle any initial state."""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

def clean(text):
    return ANSI.sub("", text)

ser = serial.Serial(port="COM3", baudrate=115200, bytesize=8, parity="N", stopbits=1, timeout=5)
ser.reset_input_buffer()
ser.reset_output_buffer()

def wait_for(pattern, timeout=10):
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        time.sleep(0.02)
        text = buf.decode("utf-8", errors="replace")
        c = clean(text)
        if re.search(pattern, c, re.IGNORECASE):
            return c
    return clean(buf.decode("utf-8", errors="replace"))

def send_and_wait(cmd, pattern, timeout=8):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("utf-8"))
    ser.flush()
    return wait_for(pattern, timeout)

# Step 1: Detect current state
print("Connecting...")
ser.write(b"\r\n\r\n")
ser.flush()
time.sleep(2)

buf = b""
end = time.time() + 5
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
    time.sleep(0.05)

resp = clean(buf.decode("utf-8", errors="replace"))
print(f"  State: [{resp.strip()[-60:]}]")

in_manager = "#" in resp
in_operator = ">" in resp and "#" not in resp
at_login = "Username" in resp

# Step 2: Get to manager mode
if at_login:
    print("  At login prompt, logging in...")
    ser.write(b"manager\r\n")
    ser.flush()
    resp = wait_for("Password", timeout=5)
    ser.write(b"Welcome01!\r\n")
    ser.flush()
    resp = wait_for("Kingdom", timeout=5)
    in_manager = "#" in resp
    in_operator = ">" in resp and "#" not in resp

if in_operator:
    print("  In operator mode, show commands work.")
    # Try to get manager mode for saving
    # First show the data, then try to login for save
    pass

if in_manager:
    print("  In manager mode.")

# Disable paging (works in operator mode too)
send_and_wait("terminal length 1000", "Kingdom>|Kingdom#", timeout=3)

# Step 3: Show VLAN IPs (works in operator mode)
print("\n=== VLAN IPs ===")
resp = send_and_wait("show ip", "Kingdom>|Kingdom#", timeout=5)
found_vlan50 = False
for line in resp.splitlines():
    s = line.strip()
    if re.search(r'\d+\.\d+\.\d+\.\d+', s) and "show" not in s:
        print(f"  {s}")
        if "10.50.50" in s:
            found_vlan50 = True

# Step 4: Show routes
print("\n=== IP Routes ===")
resp = send_and_wait("show ip route", "Kingdom>|Kingdom#", timeout=5)
found_new_route = False
for line in resp.splitlines():
    s = line.strip()
    if re.search(r'\d+\.\d+\.\d+\.\d+', s) and "show" not in s:
        print(f"  {s}")
        if "10.50.50.254" in s:
            found_new_route = True

# Step 5: Show VLAN 50 config
print("\n=== VLAN 50 Config ===")
resp = send_and_wait("show running-config vlan 50", "Kingdom>|Kingdom#", timeout=5)
for line in resp.splitlines():
    s = line.strip()
    if s and "show" not in s and "Running" not in s and "Main-Core" not in s:
        print(f"  {s}")

# Summary
print("\n=== Summary ===")
if found_vlan50:
    print("  VLAN 50 IP changed to 10.50.50.x - OK")
else:
    print("  VLAN 50 IP change NOT detected (may still be 192.168.1.1)")
if found_new_route:
    print("  Default route changed to 10.50.50.254 - OK")
else:
    print("  Default route change NOT detected")

# Step 6: Try to save (need manager mode)
if not in_manager:
    print("\n  Attempting manager login to save config...")
    # Try 'login manager' command (available from operator mode on some Aruba switches)
    resp = send_and_wait("login manager", "Password|Username", timeout=5)
    if "Password" in resp:
        ser.write(b"Welcome01!\r\n")
        ser.flush()
        resp = wait_for("Kingdom#|Invalid", timeout=5)
        in_manager = "#" in resp
    elif "Username" in resp:
        ser.write(b"manager\r\n")
        ser.flush()
        resp = wait_for("Password", timeout=3)
        ser.write(b"Welcome01!\r\n")
        ser.flush()
        resp = wait_for("Kingdom#|Invalid", timeout=5)
        in_manager = "#" in resp

if in_manager:
    print("\n=== Saving Config ===")
    ser.reset_input_buffer()
    ser.write(b"write memory\r\n")
    ser.flush()
    resp = wait_for("Success|Kingdom#|y/n", timeout=10)
    if "y/n" in resp.lower():
        ser.write(b"y\r\n")
        ser.flush()
        resp = wait_for("Success|Kingdom#", timeout=10)
    if "success" in resp.lower():
        print("  Config saved successfully.")
    else:
        print(f"  Save: [{resp.strip()[-60:]}]")
else:
    print("\n  Could not get manager mode to save.")
    print("  Changes may be in running config but not saved to startup.")

ser.close()
print("\nDone.")
