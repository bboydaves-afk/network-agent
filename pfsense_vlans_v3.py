"""Set up VLANs using pfSense option 12 (PHP shell).

Option 12 gives an interactive PHP environment with all pfSense
functions pre-loaded. No file writing or shell escaping needed.
"""

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

def php(ser, cmd, timeout=15):
    """Send PHP command to pfSense PHP shell and wait for prompt."""
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


def main():
    print("=" * 55)
    print("  pfSense VLAN Setup (PHP Shell)")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Get to menu
    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=10)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    # Enter PHP shell (option 12)
    print("[*] Entering pfSense PHP shell (option 12)...")
    send(ser, "12\n", delay=2)
    resp = read_until(ser, r"pfSense shell:", timeout=15)
    if "pfSense shell:" not in resp:
        print("[!] Could not enter PHP shell.")
        ser.close()
        return
    print("[*] PHP shell active.")
    print()

    # Parse config
    print("[1] Loading config...")
    php(ser, "parse_config(true);")
    print("  Config loaded.")
    print()

    # Initialize VLANs array
    print("[2] Initializing VLAN config...")
    php(ser, 'if (empty($config["vlans"])) $config["vlans"] = array("vlan" => array());')
    php(ser, 'if (empty($config["vlans"]["vlan"])) $config["vlans"]["vlan"] = array();')
    print("  VLAN array ready.")
    print()

    # Create each VLAN
    vlans = [
        (10,  "DATA",       "10.10.10.254",   "24", "opt1"),
        (20,  "VOICE",      "10.20.20.254",   "24", "opt2"),
        (30,  "GUEST",      "10.30.30.254",   "24", "opt3"),
        (100, "MANAGEMENT", "10.100.100.254", "24", "opt4"),
    ]

    print("[3] Creating VLANs on igb2...")
    for vid, name, ip, subnet, ifn in vlans:
        # Add VLAN definition
        php(ser, f'$config["vlans"]["vlan"][] = array("if" => "igb2", "tag" => "{vid}", "pcp" => "", "descr" => "{name}", "vlanif" => "igb2.{vid}");')
        print(f"  VLAN {vid} ({name}) created on igb2")
    print()

    # Assign interfaces
    print("[4] Assigning interfaces with IPs...")
    for vid, name, ip, subnet, ifn in vlans:
        php(ser, f'$config["interfaces"]["{ifn}"] = array("enable" => "", "if" => "igb2.{vid}", "descr" => "{name}", "ipaddr" => "{ip}", "subnet" => "{subnet}", "spoofmac" => "");')
        print(f"  {ifn} = igb2.{vid} = {ip}/{subnet} ({name})")
    print()

    # Add firewall rules
    print("[5] Adding firewall pass rules...")
    for vid, name, ip, subnet, ifn in vlans:
        tracker = 1000000 + vid
        php(ser, f'$config["filter"]["rule"][] = array("type" => "pass", "interface" => "{ifn}", "ipprotocol" => "inet", "source" => array("network" => "{ifn}"), "destination" => array("any" => ""), "descr" => "Allow {name} to any", "tracker" => {tracker});')
        print(f"  Pass rule: {name} ({ifn}) -> any")
    print()

    # Save config
    print("[6] Saving configuration...")
    out = php(ser, 'write_config("Added VLANs 10,20,30,100 with IPs and rules");', timeout=30)
    print(f"  {out.strip() if out.strip() else 'Config saved.'}")
    print()

    # Apply interfaces
    print("[7] Applying interface configurations...")
    for vid, name, ip, subnet, ifn in vlans:
        print(f"  Configuring {ifn} (igb2.{vid})...")
        php(ser, f'interface_configure("{ifn}");', timeout=30)
    print("  All interfaces configured.")
    print()

    # Reload firewall
    print("[8] Reloading firewall rules...")
    php(ser, "filter_configure();", timeout=30)
    print("  Firewall reloaded.")
    print()

    # Verify - print interface IPs
    print("[9] Verification:")
    out = php(ser, 'foreach (array("opt1","opt2","opt3","opt4") as $i) { $c = $config["interfaces"][$i]; echo $i . " = " . $c["if"] . " = " . $c["ipaddr"] . "/" . $c["subnet"] . " (" . $c["descr"] . ")\\n"; }')
    for line in out.splitlines():
        s = line.strip()
        if s and "=" in s:
            print(f"  {s}")
    print()

    # Exit PHP shell
    print("[*] Exiting PHP shell...")
    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=15)

    print()
    print("[*] Menu state:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()

    print()
    print("=" * 55)
    print("  VLAN Setup Complete")
    print("=" * 55)
    print()
    print("  WAN  (igb0)      DHCP from router")
    print("  LAN  (igb2)      192.168.1.254/24")
    print("  OPT1 (igb2.10)   10.10.10.254/24   DATA")
    print("  OPT2 (igb2.20)   10.20.20.254/24   VOICE")
    print("  OPT3 (igb2.30)   10.30.30.254/24   GUEST")
    print("  OPT4 (igb2.100)  10.100.100.254/24  MANAGEMENT")
    print()
    print("  Refresh the WebGUI - new interfaces will appear")
    print("  under Interfaces menu.")


if __name__ == "__main__":
    main()
