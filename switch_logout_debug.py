"""Debug what happens after logout on the switch."""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

ser = serial.Serial(port="COM3", baudrate=115200, bytesize=8, parity="N", stopbits=1, timeout=5)
ser.reset_input_buffer()
ser.reset_output_buffer()

# Get to prompt
print("Sending enters...")
ser.write(b"\r\n\r\n\r\n")
ser.flush()
time.sleep(3)

buf = b""
end = time.time() + 3
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
    time.sleep(0.05)

ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")
text = ANSI.sub("", buf.decode("utf-8", errors="replace"))
print(f"Current state: [{text.strip()[-60:]}]")

if ">" in text or "#" in text:
    print("\nSending logout + y...")
    ser.reset_input_buffer()
    ser.write(b"logout\r\n")
    ser.flush()
    time.sleep(1)

    # Read response
    buf = b""
    end = time.time() + 2
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        time.sleep(0.05)
    text = ANSI.sub("", buf.decode("utf-8", errors="replace"))
    print(f"After logout: [{text.strip()}]")

    if "y/n" in text.lower():
        ser.write(b"y\r\n")
        ser.flush()

    # Now read everything for 20 seconds, printing as we go
    print("\nReading post-logout output (20 seconds)...")
    total_buf = b""
    start = time.time()
    last_print = 0

    while time.time() - start < 20:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            total_buf += chunk
            now = time.time() - start
            if now - last_print > 1:
                text = ANSI.sub("", total_buf.decode("utf-8", errors="replace"))
                # Check for Username
                if "Username" in text:
                    print(f"  [{now:.1f}s] USERNAME FOUND! Total: {len(total_buf)} bytes")
                    print(f"  Last 80 chars: [{text.strip()[-80:]}]")
                    break
                else:
                    print(f"  [{now:.1f}s] {len(total_buf)} bytes, no Username yet")
                last_print = now
        time.sleep(0.01)
    else:
        text = ANSI.sub("", total_buf.decode("utf-8", errors="replace"))
        print(f"\n  Final: {len(total_buf)} bytes")
        print(f"  Content: [{text.strip()[-200:]}]")
        if "Username" in text:
            print("  USERNAME IS PRESENT!")
        else:
            print("  NO USERNAME FOUND")

elif "Username" in text:
    print("Already at login prompt.")
else:
    print(f"Unknown state.")

ser.close()
