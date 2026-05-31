"""Set up VLANs using pfSense PHP shell (option 12).

Sends PHP commands one at a time through the interactive PHP shell.
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

def php_cmd(ser, cmd, timeout=15):
    """Send a PHP command and wait for the pfSense PHP prompt."""
    ser.reset_input_buffer()
    send(ser, cmd + "\n", delay=0.3)
    # pfSense PHP shell prompt is "pfSense shell: " or just a line ending
    resp = read_until(ser, r"pfSense shell:|>>>|\n\s*$", timeout=timeout)
    return clean(resp)

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
    print("  pfSense VLAN Setup v2 (PHP Shell)")
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

    if "Enter an option:" not in resp:
        print("[!] Cannot reach pfSense menu.")
        ser.close()
        return

    # Use option 8 (Shell) and write PHP file in small pieces
    print("[*] Entering shell...")
    send(ser, "8\n", delay=2)
    read_until(ser, r"root@", timeout=10)
    time.sleep(1)
    run(ser, "export TERM=dumb")

    # Write PHP script using echo commands (more reliable than heredoc)
    print("[1] Writing VLAN script to /tmp...")

    lines = [
        '<?php',
        'require_once("config.inc");',
        'require_once("interfaces.inc");',
        'require_once("filter.inc");',
        '$config = parse_config(true);',
        '',
        '// Init VLAN array',
        'if (!isset($config["vlans"]["vlan"])) {',
        '    $config["vlans"] = array("vlan" => array());',
        '}',
        '',
        '// VLAN definitions on igb2',
        '$vdefs = array(',
        '    array(10, "DATA", "10.10.10.254", "24", "opt1"),',
        '    array(20, "VOICE", "10.20.20.254", "24", "opt2"),',
        '    array(30, "GUEST", "10.30.30.254", "24", "opt3"),',
        '    array(100, "MANAGEMENT", "10.100.100.254", "24", "opt4"),',
        ');',
        '',
        'foreach ($vdefs as $vd) {',
        '    $tag = $vd[0]; $name = $vd[1]; $ip = $vd[2];',
        '    $sub = $vd[3]; $ifn = $vd[4];',
        '',
        '    // Create VLAN',
        '    $found = false;',
        '    foreach ($config["vlans"]["vlan"] as $ev) {',
        '        if ($ev["tag"] == $tag && $ev["if"] == "igb2") {',
        '            $found = true; break;',
        '        }',
        '    }',
        '    if (!$found) {',
        '        $config["vlans"]["vlan"][] = array(',
        '            "if" => "igb2", "tag" => strval($tag),',
        '            "pcp" => "", "descr" => $name,',
        '            "vlanif" => "igb2." . $tag',
        '        );',
        '        echo "Created VLAN $tag ($name)\\n";',
        '    } else {',
        '        echo "VLAN $tag exists\\n";',
        '    }',
        '',
        '    // Assign interface',
        '    $config["interfaces"][$ifn] = array(',
        '        "enable" => "", "if" => "igb2." . $tag,',
        '        "descr" => $name, "ipaddr" => $ip,',
        '        "subnet" => $sub, "spoofmac" => "",',
        '    );',
        '    echo "  $ifn = igb2.$tag = $ip/$sub\\n";',
        '',
        '    // Add firewall rule',
        '    $config["filter"]["rule"][] = array(',
        '        "type" => "pass", "interface" => $ifn,',
        '        "ipprotocol" => "inet",',
        '        "source" => array("network" => $ifn),',
        '        "destination" => array("any" => ""),',
        '        "descr" => "Allow $name to any",',
        '        "tracker" => time() + $tag',
        '    );',
        '    echo "  Rule: Allow $name to any\\n";',
        '}',
        '',
        'echo "\\nSaving config...\\n";',
        'write_config("Added VLANs 10,20,30,100");',
        '',
        'echo "Applying interfaces...\\n";',
        'foreach ($vdefs as $vd) {',
        '    interface_configure($vd[4]);',
        '    echo "  " . $vd[4] . " configured\\n";',
        '}',
        '',
        'echo "Reloading firewall...\\n";',
        'filter_configure();',
        'echo "DONE\\n";',
        '?>',
    ]

    # Write file using echo >> approach
    run(ser, "rm -f /tmp/vlans.php")

    for i, line in enumerate(lines):
        # Escape single quotes for shell
        escaped = line.replace("'", "'\\''")
        if i == 0:
            run(ser, f"echo '{escaped}' > /tmp/vlans.php", timeout=5)
        else:
            run(ser, f"echo '{escaped}' >> /tmp/vlans.php", timeout=5)

    # Verify
    out = run(ser, "wc -l /tmp/vlans.php")
    print(f"  Written: {out.strip()}")
    print()

    # Run the script
    print("[2] Running VLAN configuration...")
    print()
    out = run(ser, "php /tmp/vlans.php", timeout=120)
    for line in out.splitlines():
        s = line.strip()
        if s:
            print(f"  {s}")
    print()

    # Verify interfaces
    print("[3] Verifying VLAN interfaces:")
    for vid in [10, 20, 30, 100]:
        out = run(ser, f"ifconfig igb2.{vid} 2>/dev/null | grep 'inet '")
        s = out.strip()
        if s:
            print(f"  igb2.{vid}: {s}")
        else:
            # Try without redirect
            ser.reset_input_buffer()
            send(ser, f"ifconfig igb2.{vid}\n", delay=0.5)
            time.sleep(2)
            buf = ""
            end = time.time() + 5
            while time.time() < end:
                if ser.in_waiting:
                    buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                    time.sleep(0.3)
                else:
                    time.sleep(0.3)
            c = clean(buf)
            for l in c.splitlines():
                if "inet " in l:
                    print(f"  igb2.{vid}: {l.strip()}")
                    break
            else:
                print(f"  igb2.{vid}: checking...")
    print()

    # Check firewall rules
    print("[4] Firewall rules per VLAN:")
    out = run(ser, "pfctl -sr | grep -c 'pass.*igb2'")
    print(f"  Total pass rules on igb2: {out.strip()}")
    print()

    # Exit shell
    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    print("[*] Final menu state:")
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
    print("  Refresh the WebGUI to see the new interfaces.")
    print("  Go to Interfaces menu to verify each VLAN.")


if __name__ == "__main__":
    main()
