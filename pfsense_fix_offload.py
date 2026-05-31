"""Make igb1 offload fix permanent and verify DHCP works."""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")


def clean(text):
    return ANSI.sub("", text)

def send(ser, text, delay=0.5):
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(delay)

def read_until(ser, pattern, timeout=30):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            buf += chunk
            c = clean(buf)
            if re.search(pattern, c, re.IGNORECASE):
                return c
        else:
            time.sleep(0.2)
    return clean(buf)

def run(ser, cmd, timeout=10):
    ser.reset_input_buffer()
    send(ser, cmd + "\n", delay=0.3)
    resp = read_until(ser, r"#\s*$", timeout=timeout)
    c = clean(resp)
    lines = []
    for line in c.splitlines():
        s = line.strip()
        if not s or "resizewin" in s or s == "78":
            continue
        if re.match(r"\[2\.8\.\d.*\].*root@", s):
            continue
        if s == cmd or s.startswith(cmd[:25]):
            continue
        lines.append(s)
    return "\n".join(lines)


def main():
    print("=" * 55)
    print("  pfSense: Permanent Offload Fix + DHCP Test")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=10)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")

    # 1. Make sure offloads are disabled right now
    print("[1] Disabling ALL offloads on igb1...")
    run(ser, "ifconfig igb1 -txcsum -rxcsum -tso4 -tso6 -lro -txcsum6 -rxcsum6")
    time.sleep(1)
    out = run(ser, "ifconfig igb1 | grep options")
    print(f"  {out.strip()}")
    print()

    # 2. Use pfSense PHP config to disable offloads permanently
    print("[2] Making offload disable permanent via config.xml...")
    php = (
        "php -r '"
        'require_once("config.inc");'
        '$config = parse_config(true);'
        # pfSense stores hardware checksum disable in system section
        '$config["system"]["disablechecksumoffloading"] = true;'
        '$config["system"]["disablesegmentationoffloading"] = true;'
        '$config["system"]["disablelargereceiveoffloading"] = true;'
        'write_config("Disabled hardware offloads for igb1 TX fix");'
        'echo "Config saved.\\n";'
        "'"
    )
    out = run(ser, php, timeout=15)
    print(f"  {out.strip()}")
    print()

    # 3. Also disable offloads on all interfaces for consistency
    print("[3] Disabling offloads on all NICs...")
    for i in range(6):
        run(ser, f"ifconfig igb{i} -txcsum -rxcsum -tso4 -tso6 -lro -txcsum6 -rxcsum6 2>/dev/null")
    print("  Done.")
    print()

    # 4. Record pre-test counters
    print("[4] Pre-test counters:")
    out = run(ser, "netstat -I igb1 -b | grep -v Name")
    for line in out.splitlines():
        s = line.strip()
        if s and "igb1" in s:
            print(f"  {s}")
    print()

    # 5. Restart DHCP
    print("[5] Restarting DHCP server...")
    out = run(ser, "pfSsh.php playback svc restart dhcpd", timeout=20)
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # 6. Reload the filter
    print("[6] Reloading firewall filter...")
    out = run(ser, "pfctl -f /tmp/rules.debug 2>&1 | tail -3", timeout=10)
    for line in out.splitlines():
        print(f"  {line.strip()}")
    # Also try the PHP way
    run(ser, "php -r 'require_once(\"config.inc\"); require_once(\"filter.inc\"); filter_configure();'", timeout=15)
    print("  Filter reloaded.")
    print()

    # 7. Wait and watch for DHCP activity
    print("[7] Watching for DHCP activity (15 seconds)...")
    print("  >>> Unplug and replug your Ethernet cable NOW <<<")
    print()

    time.sleep(15)

    # Check DHCP leases
    print("[8] DHCP leases after wait:")
    out = run(ser, "cat /var/dhcpd/var/db/dhcpd.leases 2>/dev/null")
    if out.strip() and "lease" in out.lower():
        for line in out.splitlines():
            print(f"  {line.strip()}")
    else:
        print("  (no leases yet)")
    print()

    # ARP table
    print("[9] ARP table:")
    out = run(ser, "arp -an | grep igb1")
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    # Final counters
    print("[10] Post-test counters:")
    out = run(ser, "netstat -I igb1 -b | grep -v Name")
    for line in out.splitlines():
        s = line.strip()
        if s and "igb1" in s:
            print(f"  {s}")
    print()

    # Check if igb1 is really transmitting now
    print("[11] Quick TX test - ping gateway from LAN:")
    out = run(ser, "ping -S 192.168.1.254 -c 2 -t 1 192.168.1.1", timeout=8)
    for line in out.splitlines():
        print(f"  {line.strip()}")
    print()

    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    print("[*] Current state:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("=" * 55)
    print("  On your Windows PC, run:")
    print("    ipconfig /release")
    print("    ipconfig /renew")
    print("  Then check if you got a 192.168.1.x address.")
    print("  If yes, browse to https://192.168.1.254")
    print("=" * 55)


if __name__ == "__main__":
    main()
