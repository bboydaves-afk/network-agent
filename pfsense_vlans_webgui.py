"""Configure pfSense VLANs via WebGUI HTTP API using requests."""

import requests
import re
import urllib3
import sys
import time

# Suppress SSL warnings (self-signed cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PFSENSE_URL = "https://192.168.1.254"
USERNAME = "admin"
PASSWORD = "pfsense"

# VLAN definitions: (tag, description, IP, subnet_bits, interface_name)
VLANS = [
    ("10", "DATA", "10.10.10.254", "24", "opt1"),
    ("20", "VOICE", "10.20.20.254", "24", "opt2"),
    ("30", "GUEST", "10.30.30.254", "24", "opt3"),
    ("100", "MANAGEMENT", "10.100.100.254", "24", "opt4"),
]

PARENT_IF = "igb2"


def get_csrf(session, url):
    """Extract CSRF token from a page."""
    resp = session.get(url, verify=False, timeout=30)
    resp.raise_for_status()
    # pfSense uses a hidden input named __csrf_magic
    match = re.search(r'name=["\']__csrf_magic["\']\s+value=["\']([^"\']+)["\']', resp.text)
    if match:
        return match.group(1)
    # Try alternate pattern
    match = re.search(r"csrfMagicToken\s*=\s*[\"']([^\"']+)[\"']", resp.text)
    if match:
        return match.group(1)
    return None


def login(session):
    """Login to pfSense WebGUI."""
    print("[1] Logging in to pfSense WebGUI...")
    login_url = f"{PFSENSE_URL}/index.php"
    csrf = get_csrf(session, login_url)
    if not csrf:
        print("  ERROR: Could not get CSRF token from login page.")
        return False

    data = {
        "__csrf_magic": csrf,
        "usernamefld": USERNAME,
        "passwordfld": PASSWORD,
        "login": "Sign In",
    }
    resp = session.post(login_url, data=data, verify=False, timeout=30, allow_redirects=True)
    if "Dashboard" in resp.text or "General Information" in resp.text or resp.status_code == 200:
        # Check we're actually logged in (not back at login page)
        if "Sign In" in resp.text and "usernamefld" in resp.text:
            print("  ERROR: Login failed - still on login page.")
            return False
        print("  Logged in successfully.")
        return True
    else:
        print(f"  ERROR: Login failed (status {resp.status_code}).")
        return False


def create_vlan(session, tag, descr):
    """Create a VLAN on the parent interface."""
    print(f"  Creating VLAN {tag} ({descr}) on {PARENT_IF}...")
    url = f"{PFSENSE_URL}/interfaces_vlan_edit.php"
    csrf = get_csrf(session, url)
    if not csrf:
        print(f"    ERROR: No CSRF token for VLAN {tag}")
        return False

    data = {
        "__csrf_magic": csrf,
        "if": PARENT_IF,
        "tag": tag,
        "pcp": "",
        "descr": descr,
        "save": "Save",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    if resp.status_code == 200:
        if "already exists" in resp.text.lower():
            print(f"    VLAN {tag} already exists.")
        elif "input errors" in resp.text.lower():
            # Extract error
            err_match = re.search(r'class="text-danger">(.*?)</li>', resp.text, re.DOTALL)
            if err_match:
                print(f"    Error: {err_match.group(1).strip()}")
            else:
                print(f"    Input error (VLAN may already exist).")
        else:
            print(f"    VLAN {tag} created.")
        return True
    print(f"    Failed (status {resp.status_code}).")
    return False


def assign_interface(session, vlanif, descr):
    """Assign a VLAN interface in Interface Assignments."""
    print(f"  Assigning {vlanif} as new interface...")
    url = f"{PFSENSE_URL}/interfaces_assign.php"
    csrf = get_csrf(session, url)
    if not csrf:
        print(f"    ERROR: No CSRF token")
        return False

    # Check if already assigned
    resp = session.get(url, verify=False, timeout=30)
    if vlanif in resp.text:
        # Check if it's already assigned as an interface
        # Look for it in a select that's already saved vs available
        if re.search(rf'selected.*?value="{re.escape(vlanif)}"', resp.text):
            print(f"    {vlanif} already assigned.")
            return True

    # To add a new interface, we POST with the new interface selection
    # pfSense interface assignment page has a "add" button
    data = {
        "__csrf_magic": csrf,
        "if_add": vlanif,
        "add": "Add",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    if resp.status_code == 200:
        print(f"    {vlanif} assigned.")
        return True
    print(f"    Failed (status {resp.status_code}).")
    return False


def configure_interface(session, ifname, descr, ipaddr, subnet):
    """Configure an interface with IP address."""
    print(f"  Configuring {ifname} = {descr} ({ipaddr}/{subnet})...")
    url = f"{PFSENSE_URL}/interfaces.php?if={ifname}"
    csrf = get_csrf(session, url)
    if not csrf:
        print(f"    ERROR: No CSRF token for {ifname}")
        return False

    # Get the page to find existing form fields
    resp = session.get(url, verify=False, timeout=30)

    data = {
        "__csrf_magic": csrf,
        "if": ifname,
        "enable": "yes",
        "descr": descr,
        "type": "staticv4",
        "type6": "none",
        "ipaddr": ipaddr,
        "subnet": subnet,
        "gateway": "",
        "blockpriv": "",
        "blockbogons": "",
        "spoofmac": "",
        "mtu": "",
        "mss": "",
        "mediaopt": "",
        "save": "Save",
        "apply": "",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    if resp.status_code == 200:
        if "input errors" in resp.text.lower():
            err_match = re.search(r'class="text-danger">(.*?)</li>', resp.text, re.DOTALL)
            if err_match:
                print(f"    Error: {err_match.group(1).strip()}")
            else:
                print(f"    Input error on {ifname}.")
            return False
        print(f"    {ifname} configured.")
        return True
    print(f"    Failed (status {resp.status_code}).")
    return False


def apply_changes(session, ifname):
    """Apply pending interface changes."""
    url = f"{PFSENSE_URL}/interfaces.php?if={ifname}"
    csrf = get_csrf(session, url)
    if not csrf:
        return
    resp = session.get(url, verify=False, timeout=30)
    if "apply changes" in resp.text.lower() or "applychanges" in resp.text.lower():
        data = {
            "__csrf_magic": csrf,
            "if": ifname,
            "apply": "Apply Changes",
        }
        session.post(url, data=data, verify=False, timeout=60, allow_redirects=True)
        print(f"    Changes applied for {ifname}.")


def add_firewall_rule(session, ifname, descr_name):
    """Add a pass-all rule for an interface."""
    print(f"  Adding firewall rule: Allow {descr_name} to any...")
    url = f"{PFSENSE_URL}/firewall_rules_edit.php"
    csrf = get_csrf(session, url)
    if not csrf:
        print(f"    ERROR: No CSRF token")
        return False

    data = {
        "__csrf_magic": csrf,
        "type": "pass",
        "interface": ifname,
        "ipprotocol": "inet",
        "proto": "any",
        "src": "network",
        "srcmask": "24",
        "dst": "any",
        "dstmask": "32",
        "descr": f"Allow {descr_name} to any",
        "save": "Save",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    if resp.status_code == 200:
        print(f"    Rule added for {descr_name}.")
        return True
    print(f"    Failed (status {resp.status_code}).")
    return False


def apply_firewall(session):
    """Apply pending firewall changes."""
    print("  Applying firewall rules...")
    url = f"{PFSENSE_URL}/firewall_rules.php"
    csrf = get_csrf(session, url)
    if not csrf:
        return
    data = {
        "__csrf_magic": csrf,
        "apply": "Apply Changes",
    }
    resp = session.post(url, data=data, verify=False, timeout=60, allow_redirects=True)
    if resp.status_code == 200:
        print("    Firewall rules applied.")


def main():
    print("=" * 55)
    print("  pfSense VLAN Setup via WebGUI API")
    print("=" * 55)
    print()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    # Step 1: Login
    if not login(session):
        print("\nCannot proceed without login.")
        return

    print()

    # Step 2: Create VLANs
    print("[2] Creating VLANs on parent interface igb2...")
    for tag, descr, ip, subnet, ifname in VLANS:
        create_vlan(session, tag, descr)
    print()

    # Step 3: Assign interfaces
    print("[3] Assigning VLAN interfaces...")
    for tag, descr, ip, subnet, ifname in VLANS:
        vlanif = f"{PARENT_IF}.{tag}"
        assign_interface(session, vlanif, descr)
    print()

    # Give pfSense a moment to process
    time.sleep(2)

    # Step 4: Configure each interface with IP
    print("[4] Configuring interface IPs...")
    for tag, descr, ip, subnet, ifname in VLANS:
        configure_interface(session, ifname, descr, ip, subnet)
        time.sleep(1)
    print()

    # Step 5: Apply changes for each interface
    print("[5] Applying interface changes...")
    for tag, descr, ip, subnet, ifname in VLANS:
        apply_changes(session, ifname)
        time.sleep(1)
    print()

    # Step 6: Add firewall rules
    print("[6] Adding firewall rules...")
    for tag, descr, ip, subnet, ifname in VLANS:
        add_firewall_rule(session, ifname, descr)
    apply_firewall(session)
    print()

    # Step 7: Verify by checking interface status page
    print("[7] Verification...")
    resp = session.get(f"{PFSENSE_URL}/status_interfaces.php", verify=False, timeout=30)
    if resp.status_code == 200:
        for tag, descr, ip, subnet, ifname in VLANS:
            if ip in resp.text:
                print(f"  {descr} ({ip}/{subnet}): FOUND in status page")
            else:
                print(f"  {descr} ({ip}/{subnet}): not found in status page (may need Apply)")
    print()

    print("=" * 55)
    print("  VLAN setup complete.")
    print("  Refresh WebGUI -> Interfaces to verify.")
    print("=" * 55)


if __name__ == "__main__":
    main()
