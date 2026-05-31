"""Fix switch VLAN 50 - switch is already at Username prompt."""

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

# Wake up - get to Username prompt
ser.write(b"\r\n\r\n\r\n")
ser.flush()
time.sleep(3)
buf = b""
end = time.time() + 5
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
    time.sleep(0.02)
resp = clean(buf.decode("utf-8", errors="replace"))

if "Username" not in resp:
    print(f"Not at Username. Got: [{resp.strip()[-80:]}]")
    # If at operator/manager, logout
    if ">" in resp or "#" in resp:
        ser.write(b"logout\r\ny\r\n")
        ser.flush()
        time.sleep(5)
        # Read and discard banner
        buf = b""
        end = time.time() + 15
        while time.time() < end:
            if ser.in_waiting:
                buf += ser.read(ser.in_waiting)
            time.sleep(0.02)
            if b"Username" in buf:
                break
        resp = clean(buf.decode("utf-8", errors="replace"))
        if "Username" not in resp:
            print("Cannot get to Username prompt.")
            ser.close()
            exit(1)
    else:
        ser.close()
        exit(1)

print("At Username prompt. Logging in as manager...")

# KEY: Send username, then watch for Password with 10ms polling and respond INSTANTLY
ser.reset_input_buffer()
ser.write(b"manager\r\n")
ser.flush()

# Watch for Password: prompt with very fast polling
buf = b""
end = time.time() + 5
password_sent = False
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
        if b"Password" in buf and not password_sent:
            # Send password within milliseconds of seeing the prompt
            ser.write(b"Welcome01!\r\n")
            ser.flush()
            password_sent = True
            break
    time.sleep(0.005)  # 5ms polling

if not password_sent:
    text = clean(buf.decode("utf-8", errors="replace"))
    print(f"No Password prompt. Got: [{text.strip()[-80:]}]")
    ser.close()
    exit(1)

# Wait for the prompt after login
time.sleep(2)
buf = b""
end = time.time() + 5
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
    time.sleep(0.02)

resp = clean(buf.decode("utf-8", errors="replace"))
print(f"Login result: [{resp.strip()[-80:]}]")

if "#" in resp:
    print("MANAGER MODE!")
elif ">" in resp:
    print("Operator mode only.")
    ser.close()
    exit(1)
elif "Invalid" in resp:
    print("Invalid password.")
    ser.close()
    exit(1)
else:
    print("Unknown result.")
    ser.close()
    exit(1)

# === CONFIGURE ===

def send_cmd(cmd, timeout=5):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("utf-8"))
    ser.flush()
    time.sleep(0.5)
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        time.sleep(0.02)
        text = clean(buf.decode("utf-8", errors="replace"))
        if re.search(r"Kingdom[^>]*[#>]", text):
            return text
    return clean(buf.decode("utf-8", errors="replace"))

# Disable paging
send_cmd("terminal length 1000")

# Show current VLAN 50
print("\n--- Before ---")
resp = send_cmd("show ip")
for line in resp.splitlines():
    s = line.strip()
    if "50" in s and re.search(r'\d+\.\d+\.\d+\.\d+', s):
        print(f"  VLAN 50: {s}")

# Configure
print("\n--- Configuring ---")
send_cmd("configure terminal")
send_cmd("vlan 50")
send_cmd("no ip address 192.168.1.1 255.255.255.0")
print("  Removed 192.168.1.1/24 from VLAN 50")
send_cmd("ip address 10.50.50.1 255.255.255.0")
print("  Added 10.50.50.1/24 to VLAN 50")
send_cmd("exit")  # exit vlan 50
send_cmd("no ip route 0.0.0.0 0.0.0.0 192.168.1.254")
print("  Removed old default route via 192.168.1.254")
send_cmd("ip route 0.0.0.0 0.0.0.0 10.50.50.254")
print("  Added new default route via 10.50.50.254")
send_cmd("exit")  # exit config

# Verify
print("\n--- After ---")
resp = send_cmd("show ip")
for line in resp.splitlines():
    s = line.strip()
    if re.search(r'\d+\.\d+\.\d+\.\d+', s) and "show" not in s:
        print(f"  {s}")

print()
resp = send_cmd("show ip route 0.0.0.0")
for line in resp.splitlines():
    s = line.strip()
    if "0.0.0.0" in s and "show" not in s:
        print(f"  {s}")

# Save
print("\n--- Saving ---")
ser.reset_input_buffer()
ser.write(b"write memory\r\n")
ser.flush()
time.sleep(2)
buf = b""
end = time.time() + 5
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
    time.sleep(0.02)
resp = clean(buf.decode("utf-8", errors="replace"))
if "y/n" in resp.lower():
    ser.write(b"y\r\n")
    ser.flush()
    time.sleep(3)
    buf2 = b""
    end = time.time() + 5
    while time.time() < end:
        if ser.in_waiting:
            buf2 += ser.read(ser.in_waiting)
        time.sleep(0.02)
    resp += clean(buf2.decode("utf-8", errors="replace"))

if "success" in resp.lower():
    print("  Config saved successfully!")
else:
    print(f"  Save: [{resp.strip()[-80:]}]")

ser.close()
print("\nDone.")
