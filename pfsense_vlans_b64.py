"""Set up VLANs using base64-encoded PHP script to avoid shell escaping."""

import serial
import time
import re
import os
import base64

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

PHP_SCRIPT = """<?php
require_once("config.inc");
require_once("interfaces.inc");
require_once("filter.inc");

$config = parse_config(true);

// Init VLAN array
if (!isset($config['vlans'])) {
    $config['vlans'] = array('vlan' => array());
}
if (!isset($config['vlans']['vlan'])) {
    $config['vlans']['vlan'] = array();
}

// Remove old VLAN entries for igb2 to avoid duplicates
$new_vlans = array();
foreach ($config['vlans']['vlan'] as $v) {
    if ($v['if'] != 'igb2') {
        $new_vlans[] = $v;
    }
}
$config['vlans']['vlan'] = $new_vlans;

// VLAN definitions
$vdefs = array(
    array('tag' => '10',  'descr' => 'DATA',       'ip' => '10.10.10.254',   'sub' => '24', 'ifn' => 'opt1'),
    array('tag' => '20',  'descr' => 'VOICE',      'ip' => '10.20.20.254',   'sub' => '24', 'ifn' => 'opt2'),
    array('tag' => '30',  'descr' => 'GUEST',      'ip' => '10.30.30.254',   'sub' => '24', 'ifn' => 'opt3'),
    array('tag' => '100', 'descr' => 'MANAGEMENT', 'ip' => '10.100.100.254', 'sub' => '24', 'ifn' => 'opt4'),
);

echo "Creating VLANs...\\n";
foreach ($vdefs as $vd) {
    // Add VLAN definition
    $vlan_entry = array(
        'if' => 'igb2',
        'tag' => $vd['tag'],
        'pcp' => '',
        'descr' => $vd['descr'],
        'vlanif' => 'igb2.' . $vd['tag']
    );
    $config['vlans']['vlan'][] = $vlan_entry;
    echo "  VLAN " . $vd['tag'] . " (" . $vd['descr'] . ") on igb2\\n";

    // Assign interface
    $config['interfaces'][$vd['ifn']] = array(
        'enable' => '',
        'if' => 'igb2.' . $vd['tag'],
        'descr' => $vd['descr'],
        'ipaddr' => $vd['ip'],
        'subnet' => $vd['sub'],
        'spoofmac' => ''
    );
    echo "  " . $vd['ifn'] . " = " . $vd['ip'] . "/" . $vd['sub'] . "\\n";
}

// Init filter rules if needed
if (!isset($config['filter'])) {
    $config['filter'] = array('rule' => array());
}
if (!isset($config['filter']['rule'])) {
    $config['filter']['rule'] = array();
}

// Add firewall rules
echo "\\nAdding firewall rules...\\n";
foreach ($vdefs as $vd) {
    $config['filter']['rule'][] = array(
        'type' => 'pass',
        'interface' => $vd['ifn'],
        'ipprotocol' => 'inet',
        'source' => array('network' => $vd['ifn']),
        'destination' => array('any' => ''),
        'descr' => 'Allow ' . $vd['descr'] . ' to any',
        'tracker' => strval(1000000 + intval($vd['tag']))
    );
    echo "  Pass: " . $vd['descr'] . " -> any\\n";
}

// Save
echo "\\nSaving config...\\n";
write_config("Added VLANs 10, 20, 30, 100 matching Aruba switch");
echo "Config saved.\\n";

// Create VLAN kernel interfaces
echo "\\nCreating kernel interfaces...\\n";
foreach ($config['vlans']['vlan'] as $vlan) {
    if ($vlan['if'] == 'igb2') {
        interface_vlan_configure($vlan);
        echo "  " . $vlan['vlanif'] . " created\\n";
    }
}

// Configure each interface
echo "\\nConfiguring interfaces...\\n";
foreach ($vdefs as $vd) {
    interface_configure($vd['ifn']);
    echo "  " . $vd['ifn'] . " (" . $vd['ip'] . ") configured\\n";
}

// Reload firewall
echo "\\nReloading firewall...\\n";
filter_configure();
echo "Firewall reloaded.\\n";

// Verify
echo "\\n=== VERIFICATION ===\\n";
foreach ($vdefs as $vd) {
    $iface = $vd['ifn'];
    $cfg = $config['interfaces'][$iface];
    $real_if = $cfg['if'];
    $output = shell_exec("ifconfig " . $real_if . " 2>&1 | grep 'inet '");
    echo $iface . " (" . $real_if . "): " . trim($output) . "\\n";
}

echo "\\nDONE\\n";
?>
"""


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
    print("  pfSense VLAN Setup (base64)")
    print("=" * 55)
    print()

    # Base64 encode the PHP script
    b64 = base64.b64encode(PHP_SCRIPT.encode("utf-8")).decode("ascii")
    print(f"[*] PHP script: {len(PHP_SCRIPT)} bytes -> {len(b64)} base64 chars")
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

    # Write base64 to file, then decode
    print("[1] Transferring PHP script via base64...")

    # Send in chunks to avoid serial buffer overflow
    chunk_size = 500
    run(ser, "rm -f /tmp/vlans.b64 /tmp/vlans.php")

    for i in range(0, len(b64), chunk_size):
        chunk = b64[i:i+chunk_size]
        if i == 0:
            run(ser, f"printf '{chunk}' > /tmp/vlans.b64", timeout=5)
        else:
            run(ser, f"printf '{chunk}' >> /tmp/vlans.b64", timeout=5)

    # Decode
    run(ser, "b64decode -r /tmp/vlans.b64 > /tmp/vlans.php", timeout=5)
    time.sleep(1)

    # Verify file
    out = run(ser, "wc -c /tmp/vlans.php")
    print(f"  File: {out.strip()}")

    out = run(ser, "head -1 /tmp/vlans.php")
    print(f"  First line: {out.strip()}")
    print()

    # Run the script
    print("[2] Running VLAN configuration script...")
    print("  (this may take 30-60 seconds)")
    print()

    out = run(ser, "php /tmp/vlans.php 2>&1", timeout=120)
    for line in out.splitlines():
        s = line.strip()
        if s:
            print(f"  {s}")
    print()

    # Exit shell
    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=15)

    print("[*] Menu:")
    for line in resp.splitlines():
        s = line.strip()
        if "->" in s:
            print(f"  {s}")

    ser.close()
    print()
    print("  Refresh the WebGUI to see VLANs under Interfaces.")


if __name__ == "__main__":
    main()
