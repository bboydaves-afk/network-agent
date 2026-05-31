"""Verify pfSense VLAN status - detailed check."""

import requests
import re
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PFSENSE_URL = "https://192.168.1.254"

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

# Login
resp = session.get(f"{PFSENSE_URL}/index.php", verify=False, timeout=30)
csrf = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text).group(1)
session.post(f"{PFSENSE_URL}/index.php", data={
    "__csrf_magic": csrf, "usernamefld": "admin",
    "passwordfld": "pfsense", "login": "Sign In",
}, verify=False, timeout=30, allow_redirects=True)

# Get status page raw text (strip HTML)
print("=== INTERFACE STATUS ===")
resp = session.get(f"{PFSENSE_URL}/status_interfaces.php", verify=False, timeout=30)
# Find all interface sections by looking for h2 headers with interface names
sections = re.split(r'<h\d[^>]*>', resp.text)
for section in sections:
    # Check if this section has an interface name
    for name in ["WAN", "LAN", "DATA", "VOICE", "GUEST", "MANAGEMENT"]:
        if section.startswith(name) or f">{name}<" in section[:100]:
            text = re.sub(r'<[^>]+>', ' ', section)
            text = re.sub(r'\s+', ' ', text).strip()
            # Extract key info
            print(f"\n  [{name}]")
            # Status
            status_m = re.search(r'Status\s+(up|down|no carrier)', text, re.IGNORECASE)
            if status_m:
                print(f"    Status: {status_m.group(1)}")
            # IPv4
            ipv4_m = re.search(r'IPv4 Address\s+(\d+\.\d+\.\d+\.\d+(?:/\d+)?)', text)
            if ipv4_m:
                print(f"    IPv4: {ipv4_m.group(1)}")
            else:
                ipv4_m = re.search(r'(\d+\.\d+\.\d+\.\d+/\d+)', text)
                if ipv4_m:
                    print(f"    IPv4: {ipv4_m.group(1)}")
            # Media
            media_m = re.search(r'Media\s+(.*?)(?:Channel|In/Out|$)', text)
            if media_m:
                print(f"    Media: {media_m.group(1).strip()[:60]}")
            # Packets
            pkts_m = re.search(r'In/Out packets\s+([\d,]+)\s*/\s*([\d,]+)', text)
            if pkts_m:
                print(f"    Packets In/Out: {pkts_m.group(1)}/{pkts_m.group(2)}")
            break

# ARP table
print("\n\n=== ARP TABLE ===")
resp = session.get(f"{PFSENSE_URL}/diag_arp.php", verify=False, timeout=30)
text = re.sub(r'<[^>]+>', '\t', resp.text)
for line in text.split('\n'):
    fields = [f.strip() for f in line.split('\t') if f.strip()]
    # Look for lines with IP addresses and MACs
    if fields:
        combined = ' '.join(fields)
        if re.search(r'\d+\.\d+\.\d+\.\d+', combined) and re.search(r'[0-9a-f]{2}:[0-9a-f]{2}', combined, re.I):
            print(f"  {combined[:120]}")

# Check dashboard widget for interface summary
print("\n\n=== DASHBOARD INTERFACE WIDGET ===")
resp = session.get(f"{PFSENSE_URL}/index.php", verify=False, timeout=30)
# Look for interface summary in dashboard
iface_section = re.findall(r'((?:WAN|LAN|DATA|VOICE|GUEST|MANAGEMENT)[^<]*(?:igb\d+(?:\.\d+)?)[^<]*\d+\.\d+\.\d+\.\d+[^<]*)', resp.text)
for line in iface_section:
    print(f"  {line.strip()[:100]}")

# If nothing from dashboard, try extracting from the widget HTML
if not iface_section:
    # Get all IP addresses on the page with context
    ips = re.findall(r'(\d+\.\d+\.\d+\.\d+(?:/\d+)?)', resp.text)
    unique_ips = []
    for ip in ips:
        if ip not in unique_ips and not ip.startswith("0.") and not ip.startswith("127."):
            unique_ips.append(ip)
    print(f"  IPs found on dashboard: {', '.join(unique_ips[:15])}")
