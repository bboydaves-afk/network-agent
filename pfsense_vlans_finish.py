"""Configure pfSense VLAN interface IPs and apply changes."""

import requests
import re
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PFSENSE_URL = "https://192.168.1.254"
USERNAME = "admin"
PASSWORD = "pfsense"

IFACES = [
    ("opt1", "DATA", "10.10.10.254", "24"),
    ("opt2", "VOICE", "10.20.20.254", "24"),
    ("opt3", "GUEST", "10.30.30.254", "24"),
    ("opt4", "MANAGEMENT", "10.100.100.254", "24"),
]


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
    print("  pfSense VLAN Interface Config + Apply")
    print("=" * 55)
    print()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    print("[1] Logging in...")
    login(session)
    print("  OK")
    print()

    # Configure each interface
    print("[2] Configuring interface IPs...")
    for ifname, descr, ipaddr, subnet in IFACES:
        print(f"  {ifname} = {descr} ({ipaddr}/{subnet})...")
        url = f"{PFSENSE_URL}/interfaces.php?if={ifname}"
        resp = session.get(url, verify=False, timeout=30)
        csrf = get_csrf(resp.text)

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

        # Check for the success message
        if "configuration has been changed" in resp.text:
            print(f"    Saved. (pending apply)")
        elif "Apply" in resp.text and descr in resp.text:
            print(f"    Already configured. (pending apply)")
        else:
            # Check for actual input errors (look for the specific error list)
            err_list = re.findall(r'<li\s+class="text-danger">(.*?)</li>', resp.text)
            if err_list:
                for e in err_list:
                    print(f"    ERROR: {e}")
            else:
                print(f"    Done.")
        time.sleep(1)
    print()

    # Apply changes for each interface
    print("[3] Applying changes...")
    for ifname, descr, ipaddr, subnet in IFACES:
        url = f"{PFSENSE_URL}/interfaces.php?if={ifname}"
        resp = session.get(url, verify=False, timeout=30)

        if "apply changes" in resp.text.lower() or "must be applied" in resp.text.lower():
            csrf = get_csrf(resp.text)
            data = {
                "__csrf_magic": csrf,
                "if": ifname,
                "apply": "Apply Changes",
            }
            resp = session.post(url, data=data, verify=False, timeout=60, allow_redirects=True)
            print(f"  {ifname} ({descr}): applied.")
        else:
            print(f"  {ifname} ({descr}): no pending changes.")
        time.sleep(2)
    print()

    # Verify via status page
    print("[4] Verification (interface status)...")
    resp = session.get(f"{PFSENSE_URL}/status_interfaces.php", verify=False, timeout=30)

    for ifname, descr, ipaddr, subnet in IFACES:
        if ipaddr in resp.text:
            print(f"  {descr}: {ipaddr}/{subnet} - ACTIVE")
        else:
            print(f"  {descr}: {ipaddr}/{subnet} - not found in status (may still be initializing)")

    # Also check the main interfaces
    if "192.168.1.254" in resp.text:
        print(f"  LAN: 192.168.1.254/24 - ACTIVE")

    # Check for WAN
    wan_match = re.search(r'(\d+\.\d+\.\d+\.\d+)/\d+.*?WAN', resp.text, re.DOTALL)
    if not wan_match:
        wan_match = re.search(r'WAN.*?(\d+\.\d+\.\d+\.\d+)/\d+', resp.text, re.DOTALL)
    if wan_match:
        print(f"  WAN: {wan_match.group(1)} - ACTIVE")

    print()
    print("=" * 55)
    print("  VLAN interfaces configured and applied.")
    print("  Check WebGUI -> Status -> Interfaces to verify.")
    print("=" * 55)


if __name__ == "__main__":
    main()
