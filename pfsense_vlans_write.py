"""Write PHP VLAN script byte-by-byte via serial using printf hex escapes."""

import serial
import time
import re
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

SERIAL_PORT = "COM3"
BAUDRATE = 115200
ANSI = re.compile(r"\x1b\[\??[0-9;]*[A-Za-z]|\x1b[A-Z]|\x00")

# The PHP script to create VLANs
PHP_SCRIPT = r"""<?php
require_once("config.inc");
require_once("interfaces.inc");
require_once("filter.inc");
$config = parse_config(true);
if (!is_array($config['vlans'])) $config['vlans'] = array('vlan' => array());
if (!is_array($config['vlans']['vlan'])) $config['vlans']['vlan'] = array();
$nv = array();
foreach ($config['vlans']['vlan'] as $v) {
  if ($v['if'] != 'igb2') $nv[] = $v;
}
$config['vlans']['vlan'] = $nv;
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
if (!is_array($config['filter'])) $config['filter'] = array('rule'=>array());
if (!is_array($config['filter']['rule'])) $config['filter']['rule'] = array();
foreach ($vs as $d) {
  $config['filter']['rule'][] = array('type'=>'pass','interface'=>$d[4],'ipprotocol'=>'inet','source'=>array('network'=>$d[4]),'destination'=>array('any'=>''),'descr'=>'Allow '.$d[1].' to any','tracker'=>strval(1000000+intval($d[0])));
}
echo "Saving...\n";
write_config("Added VLANs");
echo "Creating interfaces...\n";
foreach ($config['vlans']['vlan'] as $vl) {
  if ($vl['if'] == 'igb2') {
    interface_vlan_configure($vl);
    echo $vl['vlanif']." created\n";
  }
}
echo "Configuring IPs...\n";
foreach ($vs as $d) {
  interface_configure($d[4]);
  echo $d[4]." configured\n";
}
echo "Reloading filter...\n";
filter_configure();
echo "DONE\n";
"""


def clean(text):
    return ANSI.sub("", text)

def send(ser, text, delay=0.3):
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
        lines.append(s)
    return "\n".join(lines)


def main():
    print("=" * 55)
    print("  pfSense VLAN Setup (hex transfer)")
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

    # Convert PHP script to hex string
    hex_str = PHP_SCRIPT.encode("utf-8").hex()
    print(f"[*] PHP script: {len(PHP_SCRIPT)} bytes = {len(hex_str)} hex chars")

    # Write using printf with hex
    print("[1] Writing PHP script to /tmp/vlans.php...")
    run(ser, "rm -f /tmp/vlans.php")

    # Send hex string in chunks, decode with printf
    chunk_size = 200  # 100 bytes = 200 hex chars
    total_chunks = (len(hex_str) + chunk_size - 1) // chunk_size

    for i in range(0, len(hex_str), chunk_size):
        chunk_hex = hex_str[i:i+chunk_size]
        # Convert hex pairs to \xNN format for printf
        printf_str = ""
        for j in range(0, len(chunk_hex), 2):
            printf_str += "\\x" + chunk_hex[j:j+2]

        chunk_num = i // chunk_size + 1
        if chunk_num % 5 == 0 or chunk_num == 1:
            print(f"  Chunk {chunk_num}/{total_chunks}...")

        run(ser, f'printf "{printf_str}" >> /tmp/vlans.php', timeout=5)

    # Verify file
    out = run(ser, "wc -c /tmp/vlans.php")
    print(f"  File size: {out.strip()}")

    out = run(ser, "head -3 /tmp/vlans.php")
    for line in out.splitlines():
        s = line.strip()
        if s and "head" not in s:
            print(f"  {s}")
    print()

    # Run the script
    print("[2] Running VLAN configuration...")
    print("  (this takes 30-60 seconds)")
    print()
    out = run(ser, "php /tmp/vlans.php 2>&1", timeout=120)
    for line in out.splitlines():
        s = line.strip()
        if s and "php" not in s.lower()[:4]:
            print(f"  {s}")
    print()

    # Verify interfaces exist
    print("[3] Verification:")
    out = run(ser, "ifconfig -l")
    ifaces = out.strip()
    for vid in ["10", "20", "30", "100"]:
        exists = f"igb2.{vid}" in ifaces
        if exists:
            out2 = run(ser, f"ifconfig igb2.{vid} | grep 'inet '")
            ip = out2.strip() if out2.strip() else "(no IP)"
            print(f"  igb2.{vid}: EXISTS | {ip}")
        else:
            print(f"  igb2.{vid}: NOT FOUND")
    print()

    # Check config.xml
    print("[4] Config.xml check:")
    out = run(ser, "grep -c 'opt1' /cf/conf/config.xml")
    print(f"  opt1 refs: {out.strip()}")
    out = run(ser, "grep -c 'vlanif' /cf/conf/config.xml")
    print(f"  VLAN entries: {out.strip()}")
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
    print("  Refresh WebGUI -> Interfaces")
    print("=" * 55)


if __name__ == "__main__":
    main()
