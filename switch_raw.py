"""Raw dump of switch serial output."""

import serial
import time
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

ser = serial.Serial(port="COM3", baudrate=115200, bytesize=8, parity="N", stopbits=1, timeout=5)
ser.reset_input_buffer()

print("Sending enters...")
ser.write(b"\r\n\r\n")
ser.flush()

# Read everything for 5 seconds
print("Reading for 5 seconds...")
buf = b""
end = time.time() + 5
while time.time() < end:
    if ser.in_waiting:
        buf += ser.read(ser.in_waiting)
    time.sleep(0.05)

print(f"Got {len(buf)} bytes")
text = buf.decode("utf-8", errors="replace")
print(f"Text: [{text}]")
print()

# If at a prompt, try logout
if ">" in text or "#" in text:
    print("Sending logout...")
    ser.write(b"logout\r\n")
    ser.flush()
    time.sleep(1)

    buf = b""
    end = time.time() + 3
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        time.sleep(0.05)
    text = buf.decode("utf-8", errors="replace")
    print(f"After logout: [{text}]")

    # Send y if needed
    if "y/n" in text.lower() or "Y/N" in text:
        ser.write(b"y\r\n")
        ser.flush()

    # Wait and read for 15 seconds to see the full banner
    print("\nWaiting for login banner (15 seconds)...")
    buf = b""
    end = time.time() + 15
    while time.time() < end:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            buf += chunk
            # Print as we go
            t = chunk.decode("utf-8", errors="replace")
            if t.strip():
                print(f"  [{time.time():.1f}] {repr(t[:100])}")
        time.sleep(0.1)

    full = buf.decode("utf-8", errors="replace")
    print(f"\nFull banner ({len(full)} chars): [{full[:500]}]")

ser.close()
