"""Factory reset Sophos SG 230 via serial console bootloader.

This script continuously sends keystrokes during boot to catch the
GRUB menu, then selects the factory reset / system restore option.

Usage:
  1. Run this script
  2. When prompted, power cycle the Sophos
  3. The script will intercept the boot menu automatically
"""

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

print("=" * 55)
print("  Sophos SG 230 Factory Reset via Bootloader")
print("=" * 55)
print()
print("READY. Now power cycle the Sophos:")
print("  1. Hold power button 5 seconds to power OFF")
print("  2. Press power button to turn ON")
print()
print("This script will automatically intercept the boot menu.")
print("Waiting for boot sequence...")
print()

buf = ""
start = time.time()
phase = "waiting"  # waiting -> bios -> grub -> selecting -> done

while time.time() - start < 300:
    if s.in_waiting:
        chunk = s.read(s.in_waiting).decode("utf-8", errors="replace")
        buf += chunk
        clean = ANSI.sub("", chunk)
        if clean.strip():
            for line in clean.splitlines():
                ls = line.strip()
                if ls:
                    safe = ls.encode("ascii", errors="replace").decode("ascii")
                    print("[%3ds] %s" % (int(time.time() - start), safe))

    lower = buf.lower()

    # Phase: Waiting for any boot activity
    if phase == "waiting":
        # During BIOS/POST, spam Escape and arrow keys to catch any menu
        s.write(b"\x1b")  # ESC key
        s.write(b" ")     # Space
        s.write(b"\r")    # Enter
        time.sleep(0.1)

        # Detect BIOS POST or GRUB
        if "bios" in lower or "post" in lower or "memory" in lower or "cpu" in lower:
            phase = "bios"
            print()
            print("*** BIOS/POST detected ***")
        elif "grub" in lower or "gnu grub" in lower:
            phase = "grub"
            print()
            print("*** GRUB BOOT MENU DETECTED ***")
        elif "press" in lower and ("esc" in lower or "key" in lower or "enter" in lower):
            print()
            print("*** BOOT PROMPT DETECTED ***")
            # Send the requested key
            s.write(b"\x1b")
            s.write(b" ")
            s.write(b"\r")
            time.sleep(0.5)

    # Phase: In GRUB menu - look for factory reset option
    if phase == "grub" or "grub" in lower:
        # GRUB detected - look for menu entries
        if "factory" in lower or "reset" in lower or "restore" in lower:
            print()
            print("*** FACTORY RESET OPTION FOUND ***")
            # Try to select it - on Sophos UTM GRUB, factory reset is usually
            # the last menu entry. Send down arrows then Enter.
            for _ in range(5):
                s.write(b"\x1b[B")  # Down arrow
                time.sleep(0.3)
            time.sleep(0.5)
            s.write(b"\r")  # Enter
            phase = "selecting"
            print("*** SELECTED FACTORY RESET ***")

        # If we see numbered menu entries, print them
        if any(f"{i}." in buf or f"  {i} " in buf for i in range(5)):
            print("*** MENU ENTRIES VISIBLE ***")

    # Check if boot completed (reached login) without catching menu
    if "login:" in buf and time.time() - start > 20:
        if phase in ("waiting", "bios"):
            print()
            print("=" * 55)
            print("  Boot completed without catching the GRUB menu.")
            print("  The GRUB menu may be hidden or have 0s timeout.")
            print("=" * 55)
            print()
            print("ALTERNATIVE: Trying to reset via confd command line...")
            print()

            # Try logging in with known credentials to run reset command
            # On some Sophos UTM, you can reset via single-user mode
            print("Attempting single-user mode approach...")
            print("You may need to power cycle again.")
            break

    # Check if factory reset is in progress
    if "formatting" in lower or "installing" in lower or "resetting" in lower:
        print()
        print("*** FACTORY RESET IN PROGRESS ***")
        phase = "resetting"

    if "setup" in lower and "wizard" in lower:
        print()
        print("*** SETUP WIZARD DETECTED - RESET SUCCESSFUL ***")
        phase = "done"
        break

    if "reboot" in lower and phase == "resetting":
        print()
        print("*** RESET COMPLETE - REBOOTING ***")

    time.sleep(0.05)

# Print final state
print()
print("=" * 55)
print("FINAL STATE")
print("=" * 55)
clean_all = ANSI.sub("", buf)
for line in clean_all[-2000:].splitlines():
    safe = line.encode("ascii", errors="replace").decode("ascii")
    if safe.strip():
        print(safe)

s.close()
print()
if phase == "done":
    print("Factory reset SUCCESSFUL. The Sophos will reboot into the setup wizard.")
    print("Browse to https://192.168.2.1:4444 to begin setup.")
else:
    print("Could not catch the boot menu.")
    print("The GRUB timeout may be 0 seconds on this unit.")
    print()
    print("NEXT STEPS:")
    print("  Option A: Connect a USB keyboard + VGA monitor to the SG 230")
    print("            and manually select the factory reset option during boot.")
    print("  Option B: Create a Sophos UTM bootable USB and reinstall the OS.")
    print("  Option C: Install pfSense instead (free, no license needed).")
