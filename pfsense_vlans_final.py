"""Set up VLANs using pfSense PHP shell - careful one-command-at-a-time."""

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

def send(ser, text, delay=0.3):
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(delay)

def wait_prompt(ser, timeout=30):
    """Wait for pfSense PHP shell prompt."""
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            buf += chunk
            c = clean(buf)
            # PHP shell prompt
            if "pfSense shell:" in c or "pfsh>" in c:
                return c
        else:
            time.sleep(0.2)
    return clean(buf)

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

def php_exec(ser, cmd, timeout=20):
    """Execute a single PHP command and return output."""
    ser.reset_input_buffer()
    # Send command
    for char in cmd:
        ser.write(char.encode("utf-8"))
        time.sleep(0.005)  # Small delay per char to avoid buffer overflow
    ser.write(b"\n")
    ser.flush()
    time.sleep(1)
    # Wait for next prompt
    resp = wait_prompt(ser, timeout=timeout)
    # Extract output (between command echo and prompt)
    c = clean(resp)
    lines = []
    for line in c.splitlines():
        s = line.strip()
        if not s:
            continue
        if "resizewin" in s or s == "78":
            continue
        if "pfSense shell:" in s:
            continue
        if s == cmd:
            continue
        lines.append(s)
    return "\n".join(lines)


def main():
    print("=" * 55)
    print("  pfSense VLAN Setup (Final)")
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
    print("[*] Entering pfSense PHP shell...")
    send(ser, "12\n")
    time.sleep(3)
    resp = wait_prompt(ser, timeout=15)
    if "pfSense shell:" not in resp and "pfsh>" not in resp:
        print("[!] PHP shell prompt not detected.")
        print(f"    Got: {repr(resp[:200])}")
        ser.close()
        return
    print("[*] PHP shell ready.")
    print()

    # Step 1: Load config
    print("[1] Loading configuration...")
    out = php_exec(ser, 'parse_config(true); echo "OK\\n";')
    print(f"  {out.strip()}")
    print()

    # Step 2: Initialize VLAN array
    print("[2] Initializing VLAN config array...")
    php_exec(ser, 'global $config;')
    out = php_exec(ser, 'if(empty($config["vlans"])){$config["vlans"]=array("vlan"=>array());}echo "OK\\n";')
    print(f"  {out.strip()}")
    out = php_exec(ser, 'if(empty($config["vlans"]["vlan"])){$config["vlans"]["vlan"]=array();}echo "OK\\n";')
    print(f"  {out.strip()}")

    # Clear existing igb2 VLANs to avoid duplicates
    php_exec(ser, '$nv=array();foreach($config["vlans"]["vlan"] as $v){if($v["if"]!="igb2")$nv[]=$v;}$config["vlans"]["vlan"]=$nv;')
    print()

    # Step 3: Create VLANs one at a time
    print("[3] Creating VLANs...")
    vlans = [
        ("10", "DATA"),
        ("20", "VOICE"),
        ("30", "GUEST"),
        ("100", "MANAGEMENT"),
    ]

    for tag, name in vlans:
        cmd = f'$config["vlans"]["vlan"][]=array("if"=>"igb2","tag"=>"{tag}","pcp"=>"","descr"=>"{name}","vlanif"=>"igb2.{tag}");echo "VLAN {tag} OK\\n";'
        out = php_exec(ser, cmd)
        print(f"  {out.strip()}")
    print()

    # Step 4: Assign interfaces
    print("[4] Assigning interfaces...")
    ifaces = [
        ("opt1", "10", "DATA", "10.10.10.254", "24"),
        ("opt2", "20", "VOICE", "10.20.20.254", "24"),
        ("opt3", "30", "GUEST", "10.30.30.254", "24"),
        ("opt4", "100", "MANAGEMENT", "10.100.100.254", "24"),
    ]

    for ifn, tag, name, ip, sub in ifaces:
        cmd = f'$config["interfaces"]["{ifn}"]=array("enable"=>"","if"=>"igb2.{tag}","descr"=>"{name}","ipaddr"=>"{ip}","subnet"=>"{sub}","spoofmac"=>"");echo "{ifn} OK\\n";'
        out = php_exec(ser, cmd)
        print(f"  {ifn} = igb2.{tag} = {ip}/{sub}: {out.strip()}")
    print()

    # Step 5: Add firewall rules
    print("[5] Adding firewall rules...")
    php_exec(ser, 'if(empty($config["filter"])){$config["filter"]=array("rule"=>array());}')
    php_exec(ser, 'if(empty($config["filter"]["rule"])){$config["filter"]["rule"]=array();}')

    for ifn, tag, name, ip, sub in ifaces:
        tracker = 1000000 + int(tag)
        cmd = f'$config["filter"]["rule"][]=array("type"=>"pass","interface"=>"{ifn}","ipprotocol"=>"inet","source"=>array("network"=>"{ifn}"),"destination"=>array("any"=>""),"descr"=>"Allow {name} to any","tracker"=>"{tracker}");echo "rule {name} OK\\n";'
        out = php_exec(ser, cmd)
        print(f"  {name}: {out.strip()}")
    print()

    # Step 6: Save config
    print("[6] Saving configuration...")
    out = php_exec(ser, 'write_config("Added VLANs matching Aruba switch");echo "SAVED\\n";', timeout=30)
    print(f"  {out.strip()}")
    print()

    # Step 7: Create kernel VLAN interfaces
    print("[7] Creating kernel VLAN interfaces...")
    for tag, name in vlans:
        cmd = f'$v=array("if"=>"igb2","tag"=>"{tag}","vlanif"=>"igb2.{tag}","pcp"=>"");interface_vlan_configure($v);echo "igb2.{tag} OK\\n";'
        out = php_exec(ser, cmd, timeout=15)
        print(f"  igb2.{tag}: {out.strip()}")
    print()

    # Step 8: Configure interfaces
    print("[8] Configuring interfaces (assigning IPs)...")
    for ifn, tag, name, ip, sub in ifaces:
        out = php_exec(ser, f'interface_configure("{ifn}");echo "{ifn} configured\\n";', timeout=30)
        print(f"  {ifn}: {out.strip()}")
    print()

    # Step 9: Reload firewall
    print("[9] Reloading firewall...")
    out = php_exec(ser, 'filter_configure();echo "FW OK\\n";', timeout=30)
    print(f"  {out.strip()}")
    print()

    # Step 10: Verify
    print("[10] Verification:")
    for tag, name in vlans:
        out = php_exec(ser, f'echo trim(shell_exec("ifconfig igb2.{tag} 2>&1 | grep inet"))."\\n";')
        s = out.strip()
        print(f"  igb2.{tag} ({name}): {s if s else '(no IP yet)'}")
    print()

    # Exit PHP shell
    print("[*] Exiting PHP shell...")
    send(ser, "exit\n")
    time.sleep(2)
    resp = read_until(ser, r"Enter an option:", timeout=15)

    print("[*] Final menu:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("=" * 55)
    print("  Refresh WebGUI -> Interfaces to see VLANs.")
    print("=" * 55)


if __name__ == "__main__":
    main()
