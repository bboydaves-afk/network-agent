"""Debug: check interface assignments and config form details."""

import requests
import re
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PFSENSE_URL = "https://192.168.1.254"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
})

# Login
resp = session.get(f"{PFSENSE_URL}/index.php", verify=False, timeout=30)
csrf = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
session.post(f"{PFSENSE_URL}/index.php", data={
    "__csrf_magic": csrf,
    "usernamefld": "admin",
    "passwordfld": "pfsense",
    "login": "Sign In",
}, verify=False, timeout=30, allow_redirects=True)

# Check assignments page
print("=== INTERFACE ASSIGNMENTS PAGE ===")
resp = session.get(f"{PFSENSE_URL}/interfaces_assign.php", verify=False, timeout=30)

# Look for all links to interfaces.php
links = re.findall(r'interfaces\.php\?if=([^"&]+)', resp.text)
print(f"Interface links found: {links}")

# Look for table rows with interface info
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
print(f"Table rows: {len(rows)}")
for i, row in enumerate(rows):
    if 'igb' in row or 'opt' in row or 'wan' in row or 'lan' in row:
        # Clean HTML
        text = re.sub(r'<[^>]+>', ' ', row)
        text = ' '.join(text.split())
        print(f"  Row {i}: {text[:200]}")

# Look for available interfaces dropdown
avail = re.findall(r'<option\s+value="([^"]*)"[^>]*>([^<]*)</option>', resp.text)
print(f"\nAll select options ({len(avail)}):")
for val, label in avail:
    print(f"  '{val}' = '{label}'")

# Check VLANs page to see if VLANs were created
print("\n=== VLANS PAGE ===")
resp = session.get(f"{PFSENSE_URL}/interfaces_vlan.php", verify=False, timeout=30)
vlan_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
for i, row in enumerate(vlan_rows):
    if 'igb' in row or 'vlan' in row.lower():
        text = re.sub(r'<[^>]+>', ' ', row)
        text = ' '.join(text.split())
        if text.strip():
            print(f"  {text[:200]}")

# Now try opt1 page and attempt a POST with debug
print("\n=== INTERFACE opt1 POST DEBUG ===")
resp = session.get(f"{PFSENSE_URL}/interfaces.php?if=opt1", verify=False, timeout=30)

# Check the page title to see what this interface is called
title = re.search(r'<h2[^>]*>(.*?)</h2>', resp.text)
if title:
    print(f"Page h2: {title.group(1).strip()}")
title = re.search(r'<title[^>]*>(.*?)</title>', resp.text)
if title:
    print(f"Page title: {title.group(1).strip()}")

# Look for the type select and its options
type_section = re.search(r'name=["\']type["\'][^>]*>(.*?)</select>', resp.text, re.DOTALL)
if type_section:
    type_opts = re.findall(r'<option\s+value="([^"]*)"[^>]*>([^<]*)</option>', type_section.group(1))
    print(f"\nIPv4 type options:")
    for val, label in type_opts:
        print(f"  '{val}' = '{label}'")

# Check subnet select options
subnet_section = re.search(r'name=["\']subnet["\'][^>]*>(.*?)</select>', resp.text, re.DOTALL)
if subnet_section:
    subnet_opts = re.findall(r'<option\s+value="([^"]*)"[^>]*>([^<]*)</option>', subnet_section.group(1))
    print(f"\nSubnet options (first/last 3):")
    for val, label in subnet_opts[:3]:
        print(f"  '{val}' = '{label}'")
    print("  ...")
    for val, label in subnet_opts[-3:]:
        print(f"  '{val}' = '{label}'")

# Now do a POST and capture the full response for errors
csrf2 = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
data = {
    "__csrf_magic": csrf2,
    "if": "opt1",
    "enable": "yes",
    "descr": "DATA",
    "type": "staticv4",
    "type6": "none",
    "ipaddr": "10.10.10.254",
    "subnet": "24",
    "gateway": "none",
    "blockpriv": "",
    "blockbogons": "",
    "spoofmac": "",
    "mtu": "",
    "mss": "",
    "save": "Save",
}
resp2 = session.post(f"{PFSENSE_URL}/interfaces.php?if=opt1", data=data, verify=False, timeout=30, allow_redirects=True)

# Look for any error messages in the response
errors = re.findall(r'(?:class="[^"]*(?:alert|danger|error)[^"]*"[^>]*>|<li>)(.*?)(?:</div>|</li>|</span>)', resp2.text, re.DOTALL)
print(f"\nPOST response errors:")
for err in errors:
    cleaned = re.sub(r'<[^>]+>', '', err).strip()
    if cleaned:
        print(f"  {cleaned[:200]}")

# Also look for "input errors" section
if "input errors" in resp2.text.lower():
    section = re.search(r'input errors(.*?)(?:</div>|</ul>)', resp2.text, re.DOTALL | re.IGNORECASE)
    if section:
        text = re.sub(r'<[^>]+>', ' ', section.group(1))
        print(f"  Input errors section: {' '.join(text.split())[:300]}")

# Check broader error area
danger_sections = re.findall(r'text-danger[^>]*>(.*?)</(?:span|div|li|p)', resp2.text, re.DOTALL)
for ds in danger_sections:
    cleaned = re.sub(r'<[^>]+>', '', ds).strip()
    if cleaned:
        print(f"  DANGER: {cleaned[:200]}")
