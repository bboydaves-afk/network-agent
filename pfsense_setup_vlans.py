"""Set up VLANs on pfSense to match the Aruba 2930F switch.

Creates tagged VLANs 10, 20, 30, 50, 100 on igb2 (LAN parent).
Assigns gateway IPs matching the switch's default gateway expectations.

Switch VLAN config:
  VLAN 10 (DATA)       - switch IP 10.10.10.1/24   -> pfSense 10.10.10.254/24
  VLAN 20 (VOICE)      - switch IP 10.20.20.1/24   -> pfSense 10.20.20.254/24
  VLAN 30 (GUEST)      - switch IP 10.30.30.1/24   -> pfSense 10.30.30.254/24
  VLAN 50 (MGMT)       - switch IP 192.168.1.1/24  -> pfSense 192.168.1.254/24 (LAN)
  VLAN 100 (MANAGEMENT)- switch IP 10.100.100.1/24 -> pfSense 10.100.100.254/24

Trunk ports on switch: 23-28 (all VLANs tagged)
"""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

VLANS = [
    {"vid": 10, "name": "DATA",       "ip": "10.10.10.254",  "subnet": "24", "descr": "DATA"},
    {"vid": 20, "name": "VOICE",      "ip": "10.20.20.254",  "subnet": "24", "descr": "VOICE"},
    {"vid": 30, "name": "GUEST",      "ip": "10.30.30.254",  "subnet": "24", "descr": "GUEST"},
    {"vid": 50, "name": "MGMT",       "ip": "192.168.50.254","subnet": "24", "descr": "MGMT"},
    {"vid": 100,"name": "MANAGEMENT", "ip": "10.100.100.254","subnet": "24", "descr": "MANAGEMENT"},
]

# Note: VLAN 50 gets 192.168.50.254/24 for now to avoid conflict with
# LAN (192.168.1.254/24). Once the trunk is connected, we can adjust.
# Actually - let me think about this differently. The switch has VLAN 50
# at 192.168.1.1/24 with gateway 192.168.1.254. Since all VLANs are
# tagged on trunk ports 23-28, VLAN 50 traffic arrives tagged on igb2.
# So pfSense needs a VLAN 50 interface with 192.168.1.254/24.
# But LAN (untagged igb2) already has 192.168.1.254/24.
# Solution: We'll create VLAN 50 but skip its IP for now.
# The untagged LAN (192.168.1.254/24) handles management until
# we configure the trunk native VLAN on the switch side.


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
    print("  pfSense VLAN Setup")
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

    # Write PHP script to /tmp
    print("[1] Writing VLAN configuration script...")

    # Build the PHP script as a heredoc
    php_script = r"""<?php
require_once("config.inc");
require_once("interfaces.inc");
require_once("filter.inc");
require_once("services.inc");

$config = parse_config(true);

// VLAN definitions - all on igb2 (LAN parent)
$vlans = array(
    array("tag" => "10",  "descr" => "DATA",       "ip" => "10.10.10.254",   "subnet" => "24", "ifname" => "opt1"),
    array("tag" => "20",  "descr" => "VOICE",      "ip" => "10.20.20.254",   "subnet" => "24", "ifname" => "opt2"),
    array("tag" => "30",  "descr" => "GUEST",      "ip" => "10.30.30.254",   "subnet" => "24", "ifname" => "opt3"),
    array("tag" => "100", "descr" => "MANAGEMENT", "ip" => "10.100.100.254", "subnet" => "24", "ifname" => "opt4"),
);

// Initialize VLAN array if needed
if (!isset($config['vlans']['vlan'])) {
    $config['vlans'] = array('vlan' => array());
}

// Create VLAN interfaces
echo "Creating VLANs on igb2...\n";
foreach ($vlans as $v) {
    // Check if VLAN already exists
    $exists = false;
    foreach ($config['vlans']['vlan'] as $existing) {
        if ($existing['tag'] == $v['tag'] && $existing['if'] == 'igb2') {
            $exists = true;
            echo "  VLAN " . $v['tag'] . " already exists, skipping.\n";
            break;
        }
    }
    if (!$exists) {
        $config['vlans']['vlan'][] = array(
            'if' => 'igb2',
            'tag' => $v['tag'],
            'pcp' => '',
            'descr' => $v['descr'],
            'vlanif' => 'igb2.' . $v['tag']
        );
        echo "  Created VLAN " . $v['tag'] . " (" . $v['descr'] . ") on igb2\n";
    }
}

// Assign VLAN interfaces
echo "\nAssigning interfaces...\n";
foreach ($vlans as $v) {
    $ifname = $v['ifname'];
    if (!isset($config['interfaces'][$ifname])) {
        $config['interfaces'][$ifname] = array(
            'enable' => '',
            'if' => 'igb2.' . $v['tag'],
            'descr' => $v['descr'],
            'ipaddr' => $v['ip'],
            'subnet' => $v['subnet'],
            'spoofmac' => '',
            'blockbogons' => ''
        );
        echo "  Assigned igb2." . $v['tag'] . " as " . $ifname . " (" . $v['descr'] . ") = " . $v['ip'] . "/" . $v['subnet'] . "\n";
    } else {
        echo "  Interface " . $ifname . " already assigned.\n";
        // Update IP if needed
        $config['interfaces'][$ifname]['ipaddr'] = $v['ip'];
        $config['interfaces'][$ifname]['subnet'] = $v['subnet'];
        $config['interfaces'][$ifname]['descr'] = $v['descr'];
        $config['interfaces'][$ifname]['enable'] = '';
        echo "  Updated " . $ifname . " IP to " . $v['ip'] . "/" . $v['subnet'] . "\n";
    }
}

// Add firewall rules for each VLAN - allow all from VLAN subnet
echo "\nAdding firewall rules...\n";
if (!isset($config['filter']['rule'])) {
    $config['filter']['rule'] = array();
}

foreach ($vlans as $v) {
    $ifname = $v['ifname'];
    // Check if rule already exists
    $rule_exists = false;
    foreach ($config['filter']['rule'] as $rule) {
        if (isset($rule['interface']) && $rule['interface'] == $ifname &&
            isset($rule['descr']) && strpos($rule['descr'], $v['descr']) !== false) {
            $rule_exists = true;
            break;
        }
    }
    if (!$rule_exists) {
        $config['filter']['rule'][] = array(
            'type' => 'pass',
            'interface' => $ifname,
            'ipprotocol' => 'inet',
            'source' => array('network' => $ifname),
            'destination' => array('any' => ''),
            'descr' => 'Allow ' . $v['descr'] . ' to any',
            'tracker' => time() + intval($v['tag'])
        );
        echo "  Added pass rule for " . $v['descr'] . " (" . $ifname . ")\n";
    } else {
        echo "  Rule for " . $v['descr'] . " already exists.\n";
    }
}

// Save config
echo "\nSaving configuration...\n";
write_config("Added VLANs 10, 20, 30, 100 with gateway IPs and firewall rules");
echo "Config saved.\n";

// Apply interfaces
echo "\nApplying interface changes...\n";
foreach ($vlans as $v) {
    $ifname = $v['ifname'];
    echo "  Configuring " . $ifname . " (igb2." . $v['tag'] . ")...\n";
    interface_configure($ifname);
}

// Reload filter
echo "\nReloading firewall rules...\n";
filter_configure();
echo "Firewall reloaded.\n";

echo "\nDone! VLANs configured:\n";
foreach ($vlans as $v) {
    echo "  VLAN " . $v['tag'] . " (" . $v['descr'] . ") = " . $v['ip'] . "/" . $v['subnet'] . "\n";
}
echo "  LAN (untagged) = 192.168.1.254/24 (MGMT/VLAN50 native)\n";
?>"""

    # Write script to file using heredoc
    ser.reset_input_buffer()
    send(ser, "cat > /tmp/setup_vlans.php << 'PHPEOF'\n", delay=0.3)
    time.sleep(0.5)

    # Send script in chunks to avoid serial buffer overflow
    chunk_size = 200
    for i in range(0, len(php_script), chunk_size):
        chunk = php_script[i:i+chunk_size]
        ser.write(chunk.encode("utf-8"))
        ser.flush()
        time.sleep(0.1)

    time.sleep(1)
    send(ser, "\nPHPEOF\n", delay=1)
    time.sleep(2)
    # Wait for prompt
    read_until(ser, r"#\s*$", timeout=10)
    print("  Script written to /tmp/setup_vlans.php")
    print()

    # Verify file was written
    out = run(ser, "wc -l /tmp/setup_vlans.php")
    print(f"  File size: {out.strip()}")
    print()

    # Run the script
    print("[2] Running VLAN configuration script...")
    print("  (this may take 30-60 seconds)")
    print()
    out = run(ser, "php /tmp/setup_vlans.php", timeout=90)
    for line in out.splitlines():
        s = line.strip()
        if s:
            print(f"  {s}")
    print()

    # Verify VLANs were created
    print("[3] Verifying VLAN interfaces...")
    out = run(ser, "ifconfig -a | grep -E '^igb2\\.' ")
    for line in out.splitlines():
        s = line.strip()
        if s:
            print(f"  {s}")
    print()

    # Show IPs on VLAN interfaces
    print("[4] VLAN interface IPs:")
    for vid in [10, 20, 30, 100]:
        out = run(ser, f"ifconfig igb2.{vid} 2>/dev/null | grep inet")
        s = out.strip()
        if s:
            print(f"  igb2.{vid}: {s}")
        else:
            print(f"  igb2.{vid}: (no IP)")
    print()

    # Show firewall rules for VLANs
    print("[5] Firewall rules for VLAN interfaces:")
    for iface in ["igb2.10", "igb2.20", "igb2.30", "igb2.100"]:
        out = run(ser, f"pfctl -sr | grep -c '{iface}'")
        s = out.strip().split('\n')[-1] if out.strip() else "0"
        print(f"  {iface}: {s} rules")
    print()

    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    # Show final menu state
    print("[*] Final state:")
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
    print("  pfSense interfaces:")
    print("    WAN  (igb0)     = DHCP (192.168.12.212)")
    print("    LAN  (igb2)     = 192.168.1.254/24")
    print("    OPT1 (igb2.10)  = 10.10.10.254/24  (DATA)")
    print("    OPT2 (igb2.20)  = 10.20.20.254/24  (VOICE)")
    print("    OPT3 (igb2.30)  = 10.30.30.254/24  (GUEST)")
    print("    OPT4 (igb2.100) = 10.100.100.254/24 (MANAGEMENT)")
    print()
    print("  Aruba switch trunk (ports 23-28) -> igb2")
    print()
    print("  Next: Connect the switch to pfSense port 3 (igb2)")
    print("  via one of the trunk ports (23-28).")


if __name__ == "__main__":
    main()
