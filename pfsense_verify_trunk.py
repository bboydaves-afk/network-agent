"""Verify trunk link between Aruba switch and pfSense."""

import requests
import re
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PFSENSE_URL = "https://192.168.1.254"
USERNAME = "admin"
PASSWORD = "pfsense"


def get_csrf(text):
    match = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


def login(session):
    resp = session.get(f"{PFSENSE_URL}/index.php", verify=False, timeout=30)
    csrf = get_csrf(resp.text)
    session.post(f"{PFSENSE_URL}/index.php", data={
        "__csrf_magic": csrf,
        "usernamefld": USERNAME,
        "passwordfld": PASSWORD,
        "login": "Sign In",
    }, verify=False, timeout=30, allow_redirects=True)


def main():
    print("=" * 55)
    print("  Trunk Link Verification")
    print("=" * 55)
    print()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    login(session)

    # 1. Check interface status
    print("[1] Interface Status:")
    resp = session.get(f"{PFSENSE_URL}/status_interfaces.php", verify=False, timeout=30)

    # Extract interface blocks - look for each interface section
    ifaces = [
        ("WAN", "igb0"),
        ("LAN", "igb2"),
        ("DATA", "igb2.10"),
        ("VOICE", "igb2.20"),
        ("GUEST", "igb2.30"),
        ("MANAGEMENT", "igb2.100"),
    ]

    for name, if_id in ifaces:
        # Look for status and IP info
        # Find the section for this interface
        section = re.search(
            rf'{name}.*?(?:Status|status).*?(?:up|down|no carrier)',
            resp.text, re.DOTALL | re.IGNORECASE
        )
        status = "unknown"
        if section:
            if "up" in section.group(0).lower():
                status = "up"
            elif "down" in section.group(0).lower() or "no carrier" in section.group(0).lower():
                status = "down"

        # Find IP
        ip_match = re.search(rf'{name}.*?(\d+\.\d+\.\d+\.\d+/\d+)', resp.text, re.DOTALL)
        ip = ip_match.group(1) if ip_match else "no IP"

        # Look for media info
        media_match = re.search(rf'{name}.*?(1000base[^\s<]*|100base[^\s<]*|10base[^\s<]*)', resp.text, re.DOTALL)
        media = media_match.group(1) if media_match else ""

        print(f"  {name:12s} ({if_id:10s}): {ip:22s} {status} {media}")

    print()

    # 2. Check ARP table
    print("[2] ARP Table:")
    resp = session.get(f"{PFSENSE_URL}/diag_arp.php", verify=False, timeout=30)

    # Extract ARP entries
    arp_entries = re.findall(
        r'(\d+\.\d+\.\d+\.\d+).*?([0-9a-f]{2}(?::[0-9a-f]{2}){5}).*?(igb\d+(?:\.\d+)?)',
        resp.text, re.IGNORECASE
    )
    if arp_entries:
        for ip, mac, iface in arp_entries:
            print(f"  {ip:20s} {mac}  ({iface})")
    else:
        print("  No ARP entries found (switch may need traffic to populate)")

    print()

    # 3. Check routing table
    print("[3] Routes:")
    resp = session.get(f"{PFSENSE_URL}/diag_routes.php", verify=False, timeout=30)

    # Look for our VLAN subnets in routes
    subnets = ["10.10.10", "10.20.20", "10.30.30", "10.100.100", "192.168.1"]
    for subnet in subnets:
        if subnet in resp.text:
            print(f"  {subnet}.0/24: route present")
        else:
            print(f"  {subnet}.0/24: no route found")

    print()

    # 4. Check firewall rules
    print("[4] Firewall Rules (per interface):")
    for ifname in ["lan", "opt1", "opt2", "opt3", "opt4"]:
        resp = session.get(f"{PFSENSE_URL}/firewall_rules.php?if={ifname}", verify=False, timeout=30)
        # Count rule rows
        rules = re.findall(r'frrule', resp.text)
        pass_rules = len(re.findall(r'fa-solid fa-check', resp.text))
        label_map = {"lan": "LAN", "opt1": "DATA", "opt2": "VOICE", "opt3": "GUEST", "opt4": "MGMT"}
        print(f"  {label_map.get(ifname, ifname):12s}: {pass_rules} pass rules")

    print()
    print("=" * 55)
    print("  Trunk verification complete.")
    print("=" * 55)


if __name__ == "__main__":
    main()
