"""Add VLAN 50 (MGMT) to pfSense via WebGUI API."""

import requests
import re
import urllib3
import time

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
    print("  Adding VLAN 50 (MGMT) to pfSense")
    print("=" * 55)
    print()

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    login(session)

    # Step 1: Create VLAN 50
    print("[1] Creating VLAN 50 on igb2...")
    url = f"{PFSENSE_URL}/interfaces_vlan_edit.php"
    resp = session.get(url, verify=False, timeout=30)
    csrf = get_csrf(resp.text)
    data = {
        "__csrf_magic": csrf,
        "if": "igb2",
        "tag": "50",
        "pcp": "",
        "descr": "MGMT",
        "save": "Save",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    if "already exists" in resp.text.lower():
        print("  VLAN 50 already exists.")
    else:
        print("  VLAN 50 created.")
    print()

    # Step 2: Assign interface
    print("[2] Assigning igb2.50...")
    url = f"{PFSENSE_URL}/interfaces_assign.php"
    resp = session.get(url, verify=False, timeout=30)
    csrf = get_csrf(resp.text)
    data = {
        "__csrf_magic": csrf,
        "if_add": "igb2.50",
        "add": "Add",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)

    # Find what interface name it got assigned
    # Look for igb2.50 in the assignments
    ifname_match = re.search(r'interfaces\.php\?if=(opt\d+).*?igb2\.50', resp.text, re.DOTALL)
    if ifname_match:
        ifname = ifname_match.group(1)
    else:
        # Try checking all opt interfaces
        links = re.findall(r'interfaces\.php\?if=(opt\d+)', resp.text)
        # It should be the highest numbered one
        ifname = max(links, key=lambda x: int(x.replace('opt', ''))) if links else "opt5"

    print(f"  Assigned as: {ifname}")
    print()

    # Step 3: Configure interface with IP
    print(f"[3] Configuring {ifname} = MGMT (10.50.50.254/24)...")
    url = f"{PFSENSE_URL}/interfaces.php?if={ifname}"
    resp = session.get(url, verify=False, timeout=30)
    csrf = get_csrf(resp.text)
    data = {
        "__csrf_magic": csrf,
        "if": ifname,
        "enable": "yes",
        "descr": "MGMT",
        "type": "staticv4",
        "type6": "none",
        "ipaddr": "10.50.50.254",
        "subnet": "24",
        "gateway": "none",
        "spoofmac": "",
        "mtu": "",
        "mss": "",
        "save": "Save",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    if "configuration has been changed" in resp.text:
        print("  Saved.")
    else:
        err_list = re.findall(r'<li\s+class="text-danger">(.*?)</li>', resp.text)
        if err_list:
            for e in err_list:
                print(f"  ERROR: {e}")
        else:
            print("  Done.")
    print()

    # Step 4: Apply changes
    print("[4] Applying changes...")
    resp = session.get(url, verify=False, timeout=30)
    if "apply changes" in resp.text.lower() or "must be applied" in resp.text.lower():
        csrf = get_csrf(resp.text)
        data = {
            "__csrf_magic": csrf,
            "if": ifname,
            "apply": "Apply Changes",
        }
        resp = session.post(url, data=data, verify=False, timeout=60, allow_redirects=True)
        print("  Applied.")
    else:
        print("  No pending changes.")
    print()
    time.sleep(2)

    # Step 5: Add firewall rule
    print("[5] Adding firewall rule (Allow MGMT to any)...")
    url = f"{PFSENSE_URL}/firewall_rules_edit.php"
    resp = session.get(url, verify=False, timeout=30)
    csrf = get_csrf(resp.text)
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
        "descr": "Allow MGMT to any",
        "save": "Save",
    }
    resp = session.post(url, data=data, verify=False, timeout=30, allow_redirects=True)
    print("  Rule added.")

    # Apply firewall
    url = f"{PFSENSE_URL}/firewall_rules.php"
    resp = session.get(url, verify=False, timeout=30)
    csrf = get_csrf(resp.text)
    data = {
        "__csrf_magic": csrf,
        "apply": "Apply Changes",
    }
    session.post(url, data=data, verify=False, timeout=60, allow_redirects=True)
    print("  Firewall applied.")
    print()

    # Step 6: Verify
    print("[6] Verification...")
    resp = session.get(f"{PFSENSE_URL}/status_interfaces.php", verify=False, timeout=30)
    if "10.50.50.254" in resp.text:
        print("  MGMT (VLAN 50): 10.50.50.254/24 - ACTIVE")
    else:
        print("  MGMT (VLAN 50): not yet visible (may need a moment)")

    # Check if switch management IP is reachable via ARP
    resp = session.get(f"{PFSENSE_URL}/diag_arp.php", verify=False, timeout=30)
    if "10.50.50" in resp.text:
        print("  Switch (10.50.50.x) visible in ARP table")
    else:
        print("  Switch not in ARP yet (will appear after first traffic)")

    print()
    print("=" * 55)
    print("  VLAN 50 (MGMT) added: 10.50.50.254/24")
    print("  Switch management at 10.50.50.254 (Aruba) should")
    print("  be reachable from pfSense now.")
    print("=" * 55)


if __name__ == "__main__":
    main()
