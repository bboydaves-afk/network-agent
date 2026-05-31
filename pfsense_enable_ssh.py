"""Enable SSH on pfSense via console option 14, then configure VLANs via SSH."""

import serial
import time
import re
import os
import subprocess
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

PFSENSE_IP = "192.168.1.254"
PFSENSE_USER = "admin"
PFSENSE_PASS = "pfsense"

PHP_SCRIPT = r"""<?php
require_once("config.inc");
require_once("interfaces.inc");
require_once("filter.inc");
$config = parse_config(true);
if (!is_array($config['vlans'])) $config['vlans'] = array('vlan' => array());
if (!is_array($config['vlans']['vlan'])) $config['vlans']['vlan'] = array();
// Remove old igb2 VLANs
$nv = array();
foreach ($config['vlans']['vlan'] as $v) {
  if ($v['if'] != 'igb2') $nv[] = $v;
}
$config['vlans']['vlan'] = $nv;
// Define VLANs
$vs = array(
  array('10','DATA','10.10.10.254','24','opt1'),
  array('20','VOICE','10.20.20.254','24','opt2'),
  array('30','GUEST','10.30.30.254','24','opt3'),
  array('100','MANAGEMENT','10.100.100.254','24','opt4'),
);
foreach ($vs as $d) {
  $config['vlans']['vlan'][] = array('if'=>'igb2','tag'=>$d[0],'pcp'=>'','descr'=>$d[1],'vlanif'=>'igb2.'.$d[0]);
  $config['interfaces'][$d[4]] = array('enable'=>'','if'=>'igb2.'.$d[0],'descr'=>$d[1],'ipaddr'=>$d[2],'subnet'=>$d[3],'spoofmac'=>'');
  echo "VLAN ".$d[0]." ".$d[1]." = ".$d[2]."/".$d[3]."\n";
}
// Firewall rules
if (!is_array($config['filter'])) $config['filter'] = array('rule'=>array());
if (!is_array($config['filter']['rule'])) $config['filter']['rule'] = array();
foreach ($vs as $d) {
  $config['filter']['rule'][] = array('type'=>'pass','interface'=>$d[4],'ipprotocol'=>'inet','source'=>array('network'=>$d[4]),'destination'=>array('any'=>''),'descr'=>'Allow '.$d[1].' to any','tracker'=>strval(1000000+intval($d[0])));
}
echo "Saving config...\n";
write_config("Added VLANs matching Aruba switch");
echo "Creating VLAN interfaces...\n";
foreach ($config['vlans']['vlan'] as $vl) {
  if ($vl['if'] == 'igb2') {
    interface_vlan_configure($vl);
    echo "  ".$vl['vlanif']." created\n";
  }
}
echo "Configuring IPs...\n";
foreach ($vs as $d) {
  interface_configure($d[4]);
  echo "  ".$d[4]." = ".$d[2]."/".$d[3]." configured\n";
}
echo "Reloading firewall...\n";
filter_configure();
echo "DONE\n";
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


def main():
    print("=" * 55)
    print("  pfSense VLAN Setup via SSH")
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

    # Enable SSH (option 14)
    print("[1] Enabling SSH on pfSense...")
    send(ser, "14\n", delay=2)
    resp = read_until(ser, r"Enter an option:|enabled|already", timeout=20)
    if "enabled" in resp.lower() or "already" in resp.lower() or "sshd" in resp.lower():
        print("  SSH enabled.")
    else:
        print(f"  Response: {resp.strip()[-100:]}")

    # Wait for it to return to menu
    if "Enter an option:" not in resp:
        send(ser, "\r\n")
        resp = read_until(ser, r"Enter an option:", timeout=10)

    ser.close()
    print()

    # Write PHP script to temp file
    print("[2] Writing PHP script to local temp file...")
    php_file = os.path.join(tempfile.gettempdir(), "pfsense_vlans.php")
    with open(php_file, "w") as f:
        f.write(PHP_SCRIPT)
    print(f"  {php_file}")
    print()

    # Transfer via SCP and run via SSH
    print("[3] Transferring script via SCP...")
    # Use scp to copy file
    scp_cmd = f'scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL "{php_file}" {PFSENSE_USER}@{PFSENSE_IP}:/tmp/vlans.php'
    print(f"  {scp_cmd}")
    result = subprocess.run(scp_cmd, shell=True, capture_output=True, text=True,
                          input=f"{PFSENSE_PASS}\n", timeout=30)
    if result.returncode == 0:
        print("  Transfer successful.")
    else:
        print(f"  SCP failed: {result.stderr}")
        print()
        print("  Trying alternative: SSH with inline script...")
        # Fall back to piping via SSH
        ssh_cmd = f'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL {PFSENSE_USER}@{PFSENSE_IP} "cat > /tmp/vlans.php" < "{php_file}"'
        result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True,
                              input=f"{PFSENSE_PASS}\n", timeout=30)
        if result.returncode != 0:
            print(f"  SSH also failed: {result.stderr}")
            print()
            print("  Try manually: ssh admin@192.168.1.254")
            print("  Password: pfsense")
            return
    print()

    # Run the script via SSH
    print("[4] Running VLAN configuration via SSH...")
    ssh_cmd = f'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL {PFSENSE_USER}@{PFSENSE_IP} "php /tmp/vlans.php"'
    print(f"  {ssh_cmd}")
    result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True,
                          input=f"{PFSENSE_PASS}\n", timeout=120)
    if result.stdout:
        for line in result.stdout.splitlines():
            print(f"  {line}")
    if result.stderr:
        for line in result.stderr.splitlines():
            if line.strip():
                print(f"  ERR: {line}")
    print()

    # Verify via SSH
    print("[5] Verifying interfaces via SSH...")
    ssh_cmd = f'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL {PFSENSE_USER}@{PFSENSE_IP} "ifconfig -l && for i in igb2.10 igb2.20 igb2.30 igb2.100; do echo -n $i:; ifconfig $i 2>/dev/null | grep inet; done"'
    result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True,
                          input=f"{PFSENSE_PASS}\n", timeout=30)
    if result.stdout:
        for line in result.stdout.splitlines():
            print(f"  {line}")
    print()

    print("=" * 55)
    print("  Refresh WebGUI -> Interfaces to see VLANs.")
    print("=" * 55)


if __name__ == "__main__":
    main()
