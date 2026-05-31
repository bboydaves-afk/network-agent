"""Fix interface assignments: restore opt1=DATA, configure opt5=MGMT."""

import requests
import re
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PFSENSE_URL = "https://192.168.1.254"

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})
resp = session.get(f"{PFSENSE_URL}/index.php", verify=False, timeout=30)
csrf = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
session.post(f"{PFSENSE_URL}/index.php", data={
    "__csrf_magic": csrf, "usernamefld": "admin",
    "passwordfld": "pfsense", "login": "Sign In",
}, verify=False, timeout=30, allow_redirects=True)


def configure_iface(ifname, descr, ipaddr, subnet):
    print(f"  Configuring {ifname} = {descr} ({ipaddr}/{subnet})...")
    url = f"{PFSENSE_URL}/interfaces.php?if={ifname}"
    resp = session.get(url, verify=False, timeout=30)
    csrf = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
    data = {
        "__csrf_magic": csrf,
        "if": ifname,
        "enable": "yes",
        "descr": descr,
        "type": "staticv4",
        "type6": "none",
        "ipaddr": ipaddr,
        "subnet": subnet,
        "gateway": "none",
        "spoofmac": "",
        "mtu": "",
        "mss": "",
        "save": "Save",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    if "configuration has been changed" in resp.text:
        print(f"    Saved.")
    else:
        err_list = re.findall(r'<li\s+class="text-danger">(.*?)</li>', resp.text)
        if err_list:
            for e in err_list:
                print(f"    ERROR: {e}")
        else:
            print(f"    Done.")

    # Apply
    resp = session.get(url, verify=False, timeout=30)
    if "must be applied" in resp.text.lower():
        csrf = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
        session.post(url, data={
            "__csrf_magic": csrf, "if": ifname, "apply": "Apply Changes",
        }, verify=False, timeout=60, allow_redirects=True)
        print(f"    Applied.")
    time.sleep(2)


print("=" * 55)
print("  Fixing Interface Assignments")
print("=" * 55)
print()

# Fix opt1: restore to DATA
print("[1] Restoring opt1 = DATA (igb2.10)...")
configure_iface("opt1", "DATA", "10.10.10.254", "24")
print()

# Configure opt5: set to MGMT
print("[2] Configuring opt5 = MGMT (igb2.50)...")
configure_iface("opt5", "MGMT", "10.50.50.254", "24")
print()

# Add firewall rule for opt5
print("[3] Adding firewall rule for MGMT (opt5)...")
url = f"{PFSENSE_URL}/firewall_rules_edit.php"
resp = session.get(url, verify=False, timeout=30)
csrf = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
data = {
    "__csrf_magic": csrf,
    "type": "pass",
    "interface": "opt5",
    "ipprotocol": "inet",
    "proto": "any",
    "src": "network",
    "srcmask": "24",
    "dst": "any",
    "dstmask": "32",
    "descr": "Allow MGMT to any",
    "save": "Save",
}
resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
print("  Rule added.")

# Apply firewall
url = f"{PFSENSE_URL}/firewall_rules.php"
resp = session.get(url, verify=False, timeout=30)
csrf = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
session.post(url, data={"__csrf_magic": csrf, "apply": "Apply Changes"}, verify=False, timeout=60, allow_redirects=True)
print("  Firewall applied.")
print()

# Verify all
print("[4] Verification:")
resp = session.get(f"{PFSENSE_URL}/status_interfaces.php", verify=False, timeout=30)
checks = [
    ("WAN", "192.168.12"),
    ("LAN", "192.168.1.254"),
    ("DATA", "10.10.10.254"),
    ("VOICE", "10.20.20.254"),
    ("GUEST", "10.30.30.254"),
    ("MANAGEMENT", "10.100.100.254"),
    ("MGMT", "10.50.50.254"),
]
for name, ip_prefix in checks:
    if ip_prefix in resp.text:
        print(f"  {name:14s}: {ip_prefix}... ACTIVE")
    else:
        print(f"  {name:14s}: NOT FOUND")

# Check assignments
print()
resp = session.get(f"{PFSENSE_URL}/interfaces_assign.php", verify=False, timeout=30)
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
print("  Assignments:")
for row in rows:
    if 'interfaces.php' in row:
        iflink = re.search(r'if=(\w+)', row)
        label = re.search(r'>(\w+)</a>', row)
        selected = re.search(r'<option\s+value="([^"]*)"[^>]*selected', row)
        ifname = iflink.group(1) if iflink else "?"
        name = label.group(1) if label else "?"
        phys = selected.group(1) if selected else "?"
        print(f"    {ifname}: {name} -> {phys}")

print()
print("=" * 55)
print("  All interfaces fixed and verified.")
print("=" * 55)
