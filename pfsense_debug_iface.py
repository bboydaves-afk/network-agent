"""Debug: fetch the interface config page to see required form fields."""

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

# Fetch opt1 interface page
resp = session.get(f"{PFSENSE_URL}/interfaces.php?if=opt1", verify=False, timeout=30)

# Extract all form inputs
inputs = re.findall(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*(?:value=["\']([^"\']*)["\'])?[^>]*>', resp.text)
selects = re.findall(r'<select[^>]*name=["\']([^"\']+)["\']', resp.text)

# Also look for error messages
errors = re.findall(r'class="text-danger"[^>]*>(.*?)</(?:li|span|div)', resp.text, re.DOTALL)

print("=== FORM INPUTS ===")
for name, value in inputs:
    if name.startswith("__csrf"):
        continue
    print(f"  {name} = '{value}'")

print()
print("=== SELECT FIELDS ===")
for name in selects:
    print(f"  {name}")

print()
print("=== ERRORS ===")
for err in errors:
    print(f"  {err.strip()}")

# Also check what the page title / interface name shows
title_match = re.search(r'<h1[^>]*>(.*?)</h1>', resp.text)
if title_match:
    print(f"\nPage title: {title_match.group(1).strip()}")

# Check if interface exists
if "Interface not found" in resp.text or "not found" in resp.text.lower():
    print("\nWARNING: Interface opt1 not found!")

# Let's also check the assignments page to see what interfaces exist
resp2 = session.get(f"{PFSENSE_URL}/interfaces_assign.php", verify=False, timeout=30)
# Find interface names in the assignments table
iface_rows = re.findall(r'<a href="interfaces\.php\?if=([^"]+)"[^>]*>([^<]+)</a>', resp2.text)
print("\n=== ASSIGNED INTERFACES ===")
for ifid, label in iface_rows:
    print(f"  {ifid}: {label}")
