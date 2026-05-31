"""Quick check all pfSense interfaces."""

import requests
import re
import urllib3

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

# Check assignments page
resp = session.get(f"{PFSENSE_URL}/interfaces_assign.php", verify=False, timeout=30)
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
print("=== INTERFACE ASSIGNMENTS ===")
for row in rows:
    if 'interfaces.php' in row:
        iflink = re.search(r'if=(\w+)', row)
        label = re.search(r'>(\w+)</a>', row)
        # Find which physical interface is selected
        selected = re.search(r'<option\s+value="([^"]*)"[^>]*selected', row)
        ifname = iflink.group(1) if iflink else "?"
        name = label.group(1) if label else "?"
        phys = selected.group(1) if selected else "?"
        print(f"  {ifname}: {name} -> {phys}")

# Check status page for IPs
print("\n=== INTERFACE STATUS ===")
resp = session.get(f"{PFSENSE_URL}/status_interfaces.php", verify=False, timeout=30)
sections = re.split(r'<div[^>]*class="[^"]*panel[^"]*"[^>]*>', resp.text)
for section in sections:
    for name in ["WAN", "LAN", "DATA", "VOICE", "GUEST", "MANAGEMENT", "MGMT"]:
        if re.search(rf'>\s*{name}\s*<', section):
            ip_m = re.search(r'(\d+\.\d+\.\d+\.\d+/\d+)', section)
            status_m = re.search(r'(up|down|no carrier)', section, re.I)
            ip = ip_m.group(1) if ip_m else "no IP"
            status = status_m.group(1) if status_m else "?"
            print(f"  {name}: {ip} ({status})")
            break
