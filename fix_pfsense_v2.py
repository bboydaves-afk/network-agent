"""Fix pfSense: restore WAN to DHCP, assign LAN via shell, set LAN IP.

Uses the pfSense shell (option 8) to directly edit config.xml for
reliable interface assignment, then uses option 2 for LAN IP.
"""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

LAN_IP = "192.168.1.254"
LAN_SUBNET = "24"
LAN_DHCP_START = "192.168.1.100"
LAN_DHCP_END = "192.168.1.199"


def clean(text):
    return ANSI.sub("", text)


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


def send(ser, text):
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(0.3)


def log(resp, last_n=20):
    lines = resp.strip().splitlines()
    for line in lines[-last_n:]:
        safe = line.encode("ascii", errors="replace").decode("ascii").strip()
        if safe:
            print(f"  | {safe}")


def shell_cmd(ser, cmd, timeout=10):
    """Send a command in pfSense shell and wait for next prompt."""
    send(ser, cmd + "\n")
    time.sleep(1)
    resp = read_until(ser, r"\$\s*$|#\s*$|>>>", timeout=timeout)
    return resp


def main():
    print("=" * 55)
    print("  pfSense Interface Fix v2")
    print("=" * 55)
    print()

    ser = serial.Serial(
        port=SERIAL_PORT, baudrate=BAUDRATE,
        bytesize=8, parity="N", stopbits=1, timeout=5,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # === Get to menu ===
    print("[*] Connecting to pfSense console...")
    send(ser, "\r\n")
    resp = read_until(ser, r"Enter an option:", timeout=10)
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    if "Enter an option:" not in resp:
        # Maybe at a Press ENTER prompt
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=15)

    if "Enter an option:" not in resp:
        print("[!] Cannot reach pfSense menu.")
        ser.close()
        return

    print("[*] Menu active.")
    # Show current state
    for line in resp.strip().splitlines():
        s = line.encode("ascii", errors="replace").decode("ascii").strip()
        if ("WAN" in s or "LAN" in s) and "->" in s:
            print(f"  Current: {s}")
    print()

    # === STEP 1: Restore WAN to DHCP (if currently static) ===
    if "DHCP" not in resp or "192.168.1.254" in resp:
        print("=" * 55)
        print("  STEP 1: Restore WAN to DHCP")
        print("=" * 55)

        send(ser, "2\n")
        resp = read_until(ser, r"DHCP.*\(y/n\)|number of the interface", timeout=15)

        # If multiple interfaces listed, select WAN (1)
        if "number" in resp.lower():
            send(ser, "1\n")
            resp = read_until(ser, r"DHCP.*\(y/n\)", timeout=15)

        # Set to DHCP
        send(ser, "y\n")
        resp = read_until(ser, r"IPv6.*DHCP|DHCP6", timeout=10)
        send(ser, "y\n")
        resp = read_until(ser, r"revert.*HTTP|webConfigurator", timeout=15)

        if "revert" in resp.lower():
            send(ser, "n\n")

        resp = read_until(ser, r"Press.*ENTER|Enter an option:", timeout=30)
        if "Press" in resp and "ENTER" in resp:
            send(ser, "\r\n")
            resp = read_until(ser, r"Enter an option:", timeout=30)

        print("[*] WAN restored to DHCP.")
        for line in resp.strip().splitlines():
            s = line.encode("ascii", errors="replace").decode("ascii").strip()
            if "WAN" in s and "->" in s:
                print(f"  {s}")
        print()
    else:
        print("[*] WAN already on DHCP, skipping step 1.")
        print()

    # === STEP 2: Use shell to add LAN to config.xml ===
    print("=" * 55)
    print("  STEP 2: Add LAN interface via shell")
    print("=" * 55)
    print()

    # Make sure we're at the menu
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=15)

    # Enter shell
    print("[*] Entering pfSense shell (option 8)...")
    send(ser, "8\n")
    time.sleep(2)
    resp = read_until(ser, r"\$\s*$|#\s*$", timeout=10)
    log(resp, 3)
    print()

    # Check if LAN already exists in config
    print("[*] Checking current config.xml for LAN...")
    resp = shell_cmd(ser, "grep -c '<lan>' /cf/conf/config.xml")
    log(resp, 3)

    if "<lan>" in resp or "1" in resp.strip().splitlines()[-1]:
        print("[*] LAN section already exists in config.xml.")
        # Check if it has the right interface
        resp = shell_cmd(ser, "grep -A2 '<lan>' /cf/conf/config.xml")
        log(resp, 5)
        print()
    else:
        print("[*] LAN not found in config. Adding igb1 as LAN...")

    # Use PHP to properly add the LAN interface via pfSense's config system
    # This is the most reliable method
    print("[*] Using pfSense PHP config to assign igb1 as LAN...")

    # Write a small PHP script
    php_script = """
<?php
require_once("config.inc");
require_once("interfaces.inc");
require_once("filter.inc");
require_once("shaper.inc");

// Read config
$config = parse_config(true);

// Check if LAN exists
if (!isset($config['interfaces']['lan'])) {
    echo "Adding LAN interface...\\n";
    $config['interfaces']['lan'] = array(
        'enable' => '',
        'if' => 'igb1',
        'descr' => 'LAN',
        'ipaddr' => '192.168.1.254',
        'subnet' => '24',
        'spoofmac' => '',
        'blockbogons' => ''
    );
    write_config("Added LAN interface via script");
    echo "LAN added to config.\\n";
} else {
    echo "LAN already exists: " . $config['interfaces']['lan']['if'] . "\\n";
    // Make sure it's igb1
    if ($config['interfaces']['lan']['if'] != 'igb1') {
        $config['interfaces']['lan']['if'] = 'igb1';
        write_config("Fixed LAN interface to igb1");
        echo "Updated LAN to igb1.\\n";
    }
    // Set IP if not set
    if (empty($config['interfaces']['lan']['ipaddr']) || $config['interfaces']['lan']['ipaddr'] == 'dhcp') {
        $config['interfaces']['lan']['ipaddr'] = '192.168.1.254';
        $config['interfaces']['lan']['subnet'] = '24';
        write_config("Set LAN IP to 192.168.1.254/24");
        echo "Set LAN IP.\\n";
    }
}

echo "Done.\\n";
?>
"""

    # Write PHP script to a temp file
    print("[*] Writing PHP config script...")
    # Use heredoc approach for the shell
    shell_cmd(ser, "cat > /tmp/setup_lan.php << 'PHPEOF'\n" + php_script.strip() + "\nPHPEOF", timeout=10)
    print("[*] Running PHP config script...")
    resp = shell_cmd(ser, "php /tmp/setup_lan.php", timeout=30)
    log(resp, 10)
    print()

    # Now apply the interface
    print("[*] Bringing up igb1...")
    shell_cmd(ser, "ifconfig igb1 up", timeout=5)

    # Apply interface config via PHP
    print("[*] Applying interface configuration...")
    apply_php = """php -r '
require_once("config.inc");
require_once("interfaces.inc");
interface_configure("lan");
echo "LAN interface configured.\\n";
'"""
    resp = shell_cmd(ser, apply_php, timeout=30)
    log(resp, 5)
    print()

    # Set up DHCP server on LAN
    print("[*] Configuring DHCP server on LAN...")
    dhcp_php = """php -r '
require_once("config.inc");
require_once("services.inc");
$config = parse_config(true);
$config["dhcpd"]["lan"] = array(
    "enable" => "",
    "range" => array(
        "from" => "192.168.1.100",
        "to" => "192.168.1.199"
    )
);
write_config("Enabled DHCP on LAN");
services_dhcpd_configure();
echo "DHCP configured on LAN.\\n";
'"""
    resp = shell_cmd(ser, dhcp_php, timeout=30)
    log(resp, 5)
    print()

    # Reload filter rules
    print("[*] Reloading firewall filter...")
    resp = shell_cmd(ser, "pfSsh.php playback svc restart all", timeout=60)
    # That might take too long, let's just reload filter
    resp = shell_cmd(ser, "php -r 'require_once(\"config.inc\"); require_once(\"filter.inc\"); filter_configure(); echo \"Filter reloaded.\\n\";'", timeout=30)
    log(resp, 5)
    print()

    # Verify
    print("[*] Verifying interfaces...")
    resp = shell_cmd(ser, "ifconfig igb1", timeout=5)
    log(resp, 10)
    print()

    resp = shell_cmd(ser, "grep -A5 '<lan>' /cf/conf/config.xml", timeout=5)
    print("[*] Config.xml LAN section:")
    log(resp, 10)
    print()

    # Exit shell back to menu
    print("[*] Returning to pfSense menu...")
    send(ser, "exit\n")
    time.sleep(2)
    resp = read_until(ser, r"Enter an option:", timeout=15)

    print()
    print("=" * 55)
    print("  FINAL RESULT")
    print("=" * 55)
    log(resp, 20)

    ser.close()

    if "192.168.1.254" in resp and "LAN" in resp:
        print()
        print("  SUCCESS!")
    print()
    print(f"  WAN (igb0) = DHCP from router")
    print(f"  LAN (igb1) = {LAN_IP}/{LAN_SUBNET}")
    print(f"  DHCP:        {LAN_DHCP_START} - {LAN_DHCP_END}")
    print()
    print(f"  WebGUI: https://{LAN_IP}")
    print(f"  Login:  admin / pfsense (CHANGE THIS!)")


if __name__ == "__main__":
    main()
