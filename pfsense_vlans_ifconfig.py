"""Manually bring up VLAN interfaces with ifconfig and verify config."""

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
    print("  VLAN ifconfig + config verify")
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

    # Check if VLAN interfaces exist at kernel level
    print("[1] Checking kernel VLAN interfaces...")
    out = run(ser, "ifconfig -l")
    print(f"  All interfaces: {out.strip()}")
    print()

    vlans = [
        ("igb2.10",  "10.10.10.254",   "255.255.255.0"),
        ("igb2.20",  "10.20.20.254",   "255.255.255.0"),
        ("igb2.30",  "10.30.30.254",   "255.255.255.0"),
        ("igb2.100", "10.100.100.254", "255.255.255.0"),
    ]

    # Create VLAN interfaces if they don't exist
    print("[2] Creating/configuring VLAN interfaces...")
    for vif, ip, mask in vlans:
        vid = vif.split(".")[1]
        # Check if exists
        out = run(ser, f"ifconfig {vif} 2>&1 | head -1")
        if "does not exist" in out or "no such" in out.lower():
            print(f"  Creating {vif}...")
            run(ser, f"ifconfig {vif} create")
            time.sleep(0.5)

        # Assign IP and bring up
        print(f"  {vif} = {ip}/{mask}...")
        run(ser, f"ifconfig {vif} inet {ip} netmask {mask} up")
        time.sleep(0.5)

        # Disable offloads
        run(ser, f"ifconfig {vif} -txcsum -rxcsum -tso4 -tso6 -lro 2>/dev/null")
    print()

    # Verify
    print("[3] Verification:")
    for vif, ip, mask in vlans:
        out = run(ser, f"ifconfig {vif}")
        ip_line = ""
        flags_line = ""
        for line in out.splitlines():
            if "inet " in line:
                ip_line = line.strip()
            if "flags=" in line:
                flags_line = "UP" if "UP" in line else "DOWN"
        print(f"  {vif}: {flags_line} | {ip_line}")
    print()

    # Check config.xml has the right data
    print("[4] Config.xml interface entries:")
    for opt in ["opt1", "opt2", "opt3", "opt4"]:
        out = run(ser, f"grep -A5 '<{opt}>' /cf/conf/config.xml | head -7")
        lines = [l.strip() for l in out.splitlines() if l.strip() and "<" in l]
        if lines:
            # Extract key info
            iface = ""
            addr = ""
            descr = ""
            for l in lines:
                if "<if>" in l:
                    iface = l.replace("<if>", "").replace("</if>", "")
                if "<ipaddr>" in l:
                    addr = l.replace("<ipaddr>", "").replace("</ipaddr>", "")
                if "<descr>" in l:
                    descr = l.replace("<descr>", "").replace("</descr>", "")
            print(f"  {opt}: if={iface} ip={addr} ({descr})")
        else:
            print(f"  {opt}: not found in config")
    print()

    # Check VLAN config
    print("[5] Config.xml VLAN entries:")
    out = run(ser, "grep -B1 -A4 'vlanif' /cf/conf/config.xml")
    for line in out.splitlines():
        s = line.strip()
        if s and "<" in s:
            print(f"  {s}")
    print()

    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    print("[*] Menu:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("=" * 55)
    print("  VLANs should now be visible in the WebGUI.")
    print("  Go to Interfaces > Assignments to verify.")
    print("=" * 55)


if __name__ == "__main__":
    main()
