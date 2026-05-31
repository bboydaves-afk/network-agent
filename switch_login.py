"""Clean enable login - no leftover bytes in buffer."""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

ANSI = re.compile(rb"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

def clean_bytes(buf):
    return ANSI.sub(b"", buf).decode("utf-8", errors="replace")

ser = serial.Serial(port="COM3", baudrate=115200, bytesize=8, parity="N", stopbits=1, timeout=5)
ser.reset_input_buffer()
ser.reset_output_buffer()

# Step 1: Get to operator prompt cleanly
print("Step 1: Getting to operator prompt...")
# Send one enter, wait, read
ser.write(b"\r\n")
ser.flush()
time.sleep(2)
buf = b""
end = time.time() + 3
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
    time.sleep(0.02)

text = clean_bytes(buf)

# Handle Username - login as manager (gets operator)
if "Username" in text:
    print("  At login, entering as manager...")
    ser.reset_input_buffer()
    ser.write(b"manager\r\n")
    ser.flush()
    pbuf = b""
    end = time.time() + 5
    while time.time() < end:
        if ser.in_waiting:
            pbuf += ser.read(ser.in_waiting)
        if b"Password" in pbuf:
            break
        time.sleep(0.005)
    ser.write(b"Welcome01!\r\n")
    ser.flush()
    time.sleep(3)
    buf = b""
    end = time.time() + 3
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        time.sleep(0.02)
    text = clean_bytes(buf)

if ">" not in text and "#" not in text:
    print(f"  No prompt: [{text.strip()[-60:]}]")
    ser.close()
    exit(1)

if "#" in text:
    print("  Already manager mode!")
    manager = True
else:
    print("  Operator mode.")
    manager = False

if not manager:
    # Step 2: Clean buffer completely - drain EVERYTHING
    print("\nStep 2: Cleaning buffers...")
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(2)  # Let any pending output arrive
    while ser.in_waiting:
        ser.read(ser.in_waiting)
        time.sleep(0.1)
    time.sleep(1)  # Extra wait
    while ser.in_waiting:
        ser.read(ser.in_waiting)
        time.sleep(0.1)

    # Step 3: Send ONLY "enable" (no extra enters!)
    print("Step 3: Sending enable...")
    ser.write(b"enable\r\n")
    ser.flush()

    # Step 4: Watch for Username prompt
    print("Step 4: Watching for prompts...")
    buf = b""
    end = time.time() + 5
    username_seen = False
    password_seen = False

    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)

        text = clean_bytes(buf)

        if "Username:" in text and not username_seen:
            print("  Username prompt detected!")
            # Respond with username
            time.sleep(0.02)
            ser.write(b"manager\r\n")
            ser.flush()
            username_seen = True
            print("  Sent username 'manager'")
            # Clear buf for next prompt
            buf = b""
            continue

        if "Password:" in text and username_seen and not password_seen:
            print("  Password prompt detected!")
            time.sleep(0.02)
            ser.write(b"Welcome01!\r\n")
            ser.flush()
            password_seen = True
            print("  Sent password")
            break

        time.sleep(0.005)

    if not password_seen and not username_seen:
        print(f"  No prompts seen. Raw: {repr(buf[:200])}")
    elif not password_seen:
        print(f"  No Password prompt after Username. Raw: {repr(buf[:200])}")

    # Wait for result
    time.sleep(3)
    buf = b""
    end = time.time() + 5
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        time.sleep(0.02)

    # Send enter to get prompt
    ser.write(b"\r\n")
    ser.flush()
    time.sleep(1)
    buf2 = b""
    end = time.time() + 2
    while time.time() < end:
        if ser.in_waiting:
            buf2 += ser.read(ser.in_waiting)
        time.sleep(0.02)

    combined = buf + buf2
    text = clean_bytes(combined)
    print(f"\n  Result: [{text.strip()[-100:]}]")

    if "#" in text:
        print("  MANAGER MODE!")
        manager = True
    elif "Unable" in text or "Invalid" in text:
        print("  Authentication failed.")
    elif ">" in text:
        print("  Still operator mode.")

# Phase: Configure
if manager:
    print("\n=== CONFIGURING ===")

    def cmd(c, wait=2):
        ser.reset_input_buffer()
        ser.write((c + "\r\n").encode("utf-8"))
        ser.flush()
        time.sleep(wait)
        b = b""
        end = time.time() + 2
        while time.time() < end:
            if ser.in_waiting:
                b += ser.read(ser.in_waiting)
            time.sleep(0.02)
        return clean_bytes(b)

    cmd("terminal length 1000")

    print("Configuring VLAN 50 + default route...")
    cmd("configure terminal")
    cmd("vlan 50")
    cmd("no ip address 192.168.1.1 255.255.255.0")
    cmd("ip address 10.50.50.1 255.255.255.0")
    cmd("exit")
    cmd("no ip route 0.0.0.0 0.0.0.0 192.168.1.254")
    cmd("ip route 0.0.0.0 0.0.0.0 10.50.50.254")
    cmd("exit")
    print("  Applied.")

    print("\nVerification:")
    r = cmd("show ip")
    for line in r.splitlines():
        s = line.strip()
        if re.search(r"\d+\.\d+\.\d+\.\d+", s) and "show" not in s:
            print(f"  {s}")

    r = cmd("show ip route 0.0.0.0")
    for line in r.splitlines():
        s = line.strip()
        if "0.0.0.0" in s and "show" not in s:
            print(f"  Route: {s}")

    print("\nSaving...")
    ser.reset_input_buffer()
    ser.write(b"write memory\r\n")
    ser.flush()
    time.sleep(2)
    b = b""
    end = time.time() + 5
    while time.time() < end:
        if ser.in_waiting:
            b += ser.read(ser.in_waiting)
        time.sleep(0.02)
    t = clean_bytes(b)
    if "y/n" in t.lower():
        ser.write(b"y\r\n")
        ser.flush()
        time.sleep(3)
        b2 = b""
        end = time.time() + 5
        while time.time() < end:
            if ser.in_waiting:
                b2 += ser.read(ser.in_waiting)
            time.sleep(0.02)
        t += clean_bytes(b2)
    if "success" in t.lower():
        print("  Saved!")
    else:
        for line in t.splitlines():
            if line.strip():
                print(f"  {line.strip()}")

ser.close()
print("\nDone.")
