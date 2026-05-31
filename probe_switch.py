"""Probe serial to see what the switch sends back."""

import serial
import time

ser = serial.Serial(port="COM3", baudrate=115200, bytesize=8, parity="N", stopbits=1, timeout=3)
ser.reset_input_buffer()
ser.reset_output_buffer()

print("Sending CR/LF...")
ser.write(b"\r\n")
ser.flush()
time.sleep(2)

buf = b""
while ser.in_waiting:
    buf += ser.read(ser.in_waiting)
    time.sleep(0.3)

print(f"Raw bytes ({len(buf)}): {buf[:200]}")
print(f"Decoded: {buf.decode('utf-8', errors='replace')[:200]}")
print()

# Try again
print("Sending another CR/LF...")
ser.write(b"\r\n")
ser.flush()
time.sleep(2)

buf = b""
while ser.in_waiting:
    buf += ser.read(ser.in_waiting)
    time.sleep(0.3)

print(f"Raw bytes ({len(buf)}): {buf[:200]}")
print(f"Decoded: {buf.decode('utf-8', errors='replace')[:200]}")
print()

# Try sending Enter a few times
print("Sending 3 more enters...")
for i in range(3):
    ser.write(b"\r\n")
    ser.flush()
    time.sleep(1)

time.sleep(2)
buf = b""
while ser.in_waiting:
    buf += ser.read(ser.in_waiting)
    time.sleep(0.3)

print(f"Raw bytes ({len(buf)}): {buf[:500]}")
print(f"Decoded: {buf.decode('utf-8', errors='replace')[:500]}")

# Try at 9600 baud
ser.close()
print("\n--- Trying 9600 baud ---")
ser = serial.Serial(port="COM3", baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=3)
ser.reset_input_buffer()
ser.write(b"\r\n")
ser.flush()
time.sleep(2)

buf = b""
while ser.in_waiting:
    buf += ser.read(ser.in_waiting)
    time.sleep(0.3)

print(f"Raw bytes ({len(buf)}): {buf[:200]}")
print(f"Decoded: {buf.decode('utf-8', errors='replace')[:200]}")

ser.close()
