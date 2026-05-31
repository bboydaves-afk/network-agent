"""Check igb1 link state and fix LAN firewall rules on pfSense."""

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


def send(ser, text, delay=0.5):
    ser.write(text.encode("utf-8"))
    ser.flush()
    time.sleep(delay)


def shell_cmd(ser, cmd, timeout=15):
    ser.reset_input_buffer()
    send(ser, cmd + "\n", delay=0.5)
    time.sleep(1)
    resp = read_until(ser, r"\[2\.8.*root@", timeout=timeout)
    return resp


def show(resp):
    for line in resp.strip().splitlines():
        safe = line.encode("ascii", errors="replace").decode("ascii").strip()
        if safe and "resizewin" not in safe and safe != "78":
            print(f"  {safe}")


def main():
    print("=" * 55)
    print("  pfSense LAN Fix - Rules & Link Check")
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

    # Enter shell
    print("[*] Entering shell...")
    send(ser, "8\n", delay=2)
    read_until(ser, r"\[2\.8|root@", timeout=10)
    time.sleep(1)

    # Check igb1 link
    print()
    print("[1] Checking igb1 link state...")
    ser.reset_input_buffer()
    send(ser, "ifconfig igb1 | grep -E 'status|flags|inet'\n", delay=1)
    time.sleep(2)
    buf = ""
    end = time.time() + 8
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            time.sleep(0.5)
        else:
            time.sleep(0.3)
    cleaned = clean(buf)
    show(cleaned)

    no_carrier = "no carrier" in cleaned.lower()
    if no_carrier:
        print()
        print("  *** igb1 has NO CARRIER - no cable detected! ***")
        print("  Plug an Ethernet cable into the SECOND port on the")
        print("  Sophos SG 230 (labeled port 2 / igb1).")
        print()

    # Check all firewall rules for LAN pass rules
    print()
    print("[2] Checking for LAN pass rules in firewall...")
    ser.reset_input_buffer()
    send(ser, "pfctl -sr | grep -c 'pass.*igb1'\n", delay=1)
    time.sleep(3)
    buf = ""
    end = time.time() + 5
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            time.sleep(0.3)
        else:
            time.sleep(0.3)
    cleaned = clean(buf)
    show(cleaned)

    # Also get full LAN-related rules
    print()
    print("[3] All LAN-related firewall rules:")
    ser.reset_input_buffer()
    send(ser, "pfctl -sr | grep igb1\n", delay=1)
    time.sleep(3)
    buf = ""
    end = time.time() + 5
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            time.sleep(0.3)
        else:
            time.sleep(0.3)
    cleaned = clean(buf)
    show(cleaned)

    has_pass = "pass" in cleaned.lower() and "igb1" in cleaned

    if not has_pass:
        print()
        print("  *** NO pass rules for LAN (igb1)! ***")
        print("  Adding default LAN rules via pfSense PHP...")
        print()

        # Use pfSense's PHP to add the default LAN rule properly
        # This creates the anti-lockout + default LAN-to-any rule
        php_cmd = (
            "php -r '"
            'require_once("config.inc");'
            'require_once("filter.inc");'
            "$config = parse_config(true);"
            # Add default LAN rule: pass all from LAN subnet
            'if (!isset($config["filter"]["rule"])) $config["filter"]["rule"] = array();'
            "$lan_rule = array("
            '  "type" => "pass",'
            '  "interface" => "lan",'
            '  "ipprotocol" => "inet",'
            '  "source" => array("network" => "lan"),'
            '  "destination" => array("any" => ""),'
            '  "descr" => "Default allow LAN to any rule",'
            '  "tracker" => time()'
            ");"
            '$config["filter"]["rule"][] = $lan_rule;'
            # Add anti-lockout rule
            "$anti_lockout = array("
            '  "type" => "pass",'
            '  "interface" => "lan",'
            '  "ipprotocol" => "inet",'
            '  "protocol" => "tcp",'
            '  "source" => array("network" => "lan"),'
            '  "destination" => array("address" => "lanip", "port" => "443"),'
            '  "descr" => "Anti-lockout Rule",'
            '  "tracker" => time() + 1'
            ");"
            '$config["filter"]["rule"][] = $anti_lockout;'
            'write_config("Added default LAN firewall rules");'
            "filter_configure();"
            'echo "LAN rules added and filter reloaded.\n";'
            "'"
        )

        ser.reset_input_buffer()
        send(ser, php_cmd + "\n", delay=1)
        time.sleep(8)
        buf = ""
        end = time.time() + 20
        while time.time() < end:
            if ser.in_waiting:
                buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                time.sleep(0.5)
            else:
                time.sleep(0.5)
        cleaned = clean(buf)
        show(cleaned)

        if "rules added" in cleaned.lower() or "reloaded" in cleaned.lower():
            print()
            print("  LAN firewall rules added successfully!")
        else:
            print()
            print("  Trying alternative approach...")
            # Fallback: directly add pf rules
            ser.reset_input_buffer()
            send(ser, "echo 'pass in quick on igb1 all' | pfctl -a 'lan_temp' -f -\n", delay=1)
            time.sleep(3)
            buf = ""
            end = time.time() + 5
            while time.time() < end:
                if ser.in_waiting:
                    buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                    time.sleep(0.3)
                else:
                    time.sleep(0.3)
            show(clean(buf))

        # Verify rules now
        print()
        print("[4] Verifying LAN rules after fix:")
        ser.reset_input_buffer()
        send(ser, "pfctl -sr | grep igb1\n", delay=1)
        time.sleep(3)
        buf = ""
        end = time.time() + 5
        while time.time() < end:
            if ser.in_waiting:
                buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                time.sleep(0.3)
            else:
                time.sleep(0.3)
        cleaned = clean(buf)
        show(cleaned)
    else:
        print()
        print("  LAN pass rules exist - firewall is OK.")

    # Check webgui config
    print()
    print("[5] WebGUI configuration:")
    ser.reset_input_buffer()
    send(ser, "grep -A5 '<webgui>' /cf/conf/config.xml\n", delay=1)
    time.sleep(3)
    buf = ""
    end = time.time() + 5
    while time.time() < end:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            time.sleep(0.3)
        else:
            time.sleep(0.3)
    cleaned = clean(buf)
    show(cleaned)

    # Exit
    send(ser, "exit\n", delay=2)
    resp = read_until(ser, r"Enter an option:", timeout=10)

    ser.close()

    print()
    print("=" * 55)
    print("  Summary")
    print("=" * 55)
    if no_carrier:
        print()
        print("  CABLE: No cable detected on igb1 (LAN port).")
        print("  -> Plug an Ethernet cable from your computer")
        print("     into the SECOND port on the Sophos SG 230.")
        print("  -> Your computer should get 192.168.1.x via DHCP.")
        print(f"  -> Then browse to https://192.168.1.254")
    else:
        print()
        print("  CABLE: Link detected on igb1.")
        print(f"  -> Browse to https://192.168.1.254")
        print("  -> If still blocked, check your computer has")
        print("     an IP in the 192.168.1.x range.")
    print()
    print("  Login: admin / pfsense")


if __name__ == "__main__":
    main()
