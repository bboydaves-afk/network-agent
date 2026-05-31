"""Capture Sophos SG 230 boot sequence via serial console."""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 38400
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

s = serial.Serial(SERIAL_PORT, BAUDRATE, bytesize=8, parity="N", stopbits=1, timeout=3)
s.reset_input_buffer()

print("Capturing Sophos boot sequence...")
print("If the Sophos is already rebooting, just wait.")
print()

buf = ""
start = time.time()
found_menu = False

while time.time() - start < 180:
    if s.in_waiting:
        chunk = s.read(s.in_waiting).decode("utf-8", errors="replace")
        buf += chunk
        clean = ANSI.sub("", chunk)
        if clean.strip():
            for line in clean.splitlines():
                ls = line.strip()
                if ls:
                    elapsed = int(time.time() - start)
                    # Safe print - replace any problematic chars
                    safe = ls.encode("ascii", errors="replace").decode("ascii")
                    print("[%3ds] %s" % (elapsed, safe))

        lower = buf.lower()
        if "factory" in lower and "reset" in lower:
            print()
            print("*** FACTORY RESET OPTION DETECTED ***")
            found_menu = True

        if "grub" in lower or ("press" in lower and "key" in lower):
            print()
            print("*** BOOT MENU / GRUB DETECTED ***")
            found_menu = True

    # If we see the login prompt, boot is complete
    if "login:" in buf and time.time() - start > 30:
        print()
        print("Boot complete - reached login prompt.")
        break

    time.sleep(0.1)

print()
print("=" * 60)
print("FULL BOOT LOG (last 3000 chars):")
print("=" * 60)
clean_all = ANSI.sub("", buf)
# Safe print
for line in clean_all[-3000:].splitlines():
    safe = line.encode("ascii", errors="replace").decode("ascii")
    print(safe)

s.close()
print()
print("Done.")
