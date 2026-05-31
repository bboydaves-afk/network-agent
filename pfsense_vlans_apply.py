"""Apply VLAN kernel interfaces that were added to config but not created."""

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

def php(ser, cmd, timeout=20):
    ser.reset_input_buffer()
    send(ser, cmd + "\n", delay=0.3)
    time.sleep(1)
    resp = read_until(ser, r"pfSense shell:", timeout=timeout)
    c = clean(resp)
    lines = []
    for line in c.splitlines():
        s = line.strip()
        if not s or "resizewin" in s or s == "78":
            continue
        if s == "pfSense shell:":
            continue
        if s == cmd or s.startswith(cmd[:30]):
            continue
        lines.append(s)
    return "\n".join(lines)

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
    print("  Apply VLAN Kernel Interfaces")
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

    # Enter PHP shell
    print("[*] Entering PHP shell...")
    send(ser, "12\n", delay=2)
    resp = read_until(ser, r"pfSense shell:", timeout=15)

    # Load config
    php(ser, "parse_config(true);")
    print("[*] Config loaded.")

    # Create VLAN kernel interfaces
    print()
    print("[1] Creating VLAN kernel interfaces...")
    vlans = [
        (10, "DATA"),
        (20, "VOICE"),
        (30, "GUEST"),
        (100, "MANAGEMENT"),
    ]

    for vid, name in vlans:
        # Find this VLAN in config and configure it
        out = php(ser, f'$v = array("if" => "igb2", "tag" => "{vid}", "vlanif" => "igb2.{vid}", "pcp" => ""); interface_vlan_configure($v); echo "igb2.{vid} created\\n";', timeout=15)
        for line in out.splitlines():
            s = line.strip()
            if s:
                print(f"  {s}")

    print()

    # Now configure interfaces (assign IPs)
    print("[2] Configuring interface IPs...")
    ifaces = [
        ("opt1", "igb2.10",  "10.10.10.254",   "24", "DATA"),
        ("opt2", "igb2.20",  "10.20.20.254",   "24", "VOICE"),
        ("opt3", "igb2.30",  "10.30.30.254",   "24", "GUEST"),
        ("opt4", "igb2.100", "10.100.100.254", "24", "MANAGEMENT"),
    ]

    for ifn, vlanif, ip, subnet, name in ifaces:
        out = php(ser, f'interface_configure("{ifn}"); echo "{ifn} ({vlanif}) configured\\n";', timeout=30)
        for line in out.splitlines():
            s = line.strip()
            if s:
                print(f"  {s}")

    print()

    # Reload filter
    print("[3] Reloading firewall rules...")
    php(ser, "filter_configure();", timeout=30)
    print("  Done.")
    print()

    # Exit PHP shell
    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=15)

    # Now enter regular shell to verify
    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")

    print("[4] Verification:")
    for vid in [10, 20, 30, 100]:
        out = run(ser, f"ifconfig igb2.{vid}")
        ip_found = False
        for line in out.splitlines():
            if "inet " in line:
                print(f"  igb2.{vid}: {line.strip()}")
                ip_found = True
            elif "flags=" in line and "UP" in line:
                if not ip_found:
                    print(f"  igb2.{vid}: UP (checking IP...)")
        if not ip_found:
            # Try more directly
            out2 = run(ser, f"ifconfig igb2.{vid} | grep inet")
            if "inet" in out2:
                print(f"  igb2.{vid}: {out2.strip()}")
            else:
                print(f"  igb2.{vid}: interface exists but no IP visible")

    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    print()
    print("[*] Menu:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("=" * 55)
    print("  Refresh WebGUI to see VLANs under Interfaces.")
    print("=" * 55)


if __name__ == "__main__":
    main()
