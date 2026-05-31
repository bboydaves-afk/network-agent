# Mitel IP Phone over Spectrum Community Wi-Fi - Complete Setup Guide

## Equipment Needed

| Item | Purpose |
|------|---------|
| GL.iNet GL-MT3000 (Beryl AX) | Travel router - provides NAT, DHCP, and private Wi-Fi |
| Mitel MTL-300AN WLAN Adapter (51304977) | Wireless bridge for the Mitel IP phone |
| Mitel IP Phone (6900/6800/5300 series) | Your VoIP phone |
| Ethernet cable (included with GL-MT3000) | Connects GL-MT3000 WAN to Spectrum switch |
| Network cable (included with MTL-300AN) | Used during adapter configuration |
| Laptop or PC with Wi-Fi | For configuring both devices |

## Network Diagram

```
[Spectrum Closet Switch]
       |
       | Ethernet (WAN port)
       |
  [GL-MT3000 Travel Router]
    - NAT / Firewall
    - DHCP Server (192.168.8.x)
    - Private SSID (WPA2-PSK)
       |
       | Wi-Fi (your private SSID)
       |
  [Mitel MTL-300AN WLAN Adapter]
       |
       | Ethernet (network cable)
       |
  [Mitel IP Phone]
```

---

## PHASE 1: GL.iNet GL-MT3000 Setup

### Step 1: Physical Connection

1. Assemble the two-piece power adapter that came with the GL-MT3000
2. Run an Ethernet cable from an active port on the Spectrum closet switch
   to the **2.5G WAN port** on the GL-MT3000 (the port labeled "WAN")
3. Plug the power adapter into the GL-MT3000 and connect to an outlet
4. Wait about 1 minute for the router to fully boot up

### Step 2: Connect to the GL-MT3000

1. On your laptop, open Wi-Fi settings
2. Look for the default SSID: **GL-MT3000-xxx** (the exact name and
   default Wi-Fi password are printed on the label on the bottom of
   the router)
3. Default Wi-Fi password: **goodlife** (or check the bottom label)
4. Connect to this network

### Step 3: Access the Admin Panel

1. Open a web browser (Chrome, Edge, Firefox)
2. Navigate to: **http://192.168.8.1**
3. Select your preferred language
4. Set a new admin password (minimum 5 characters) - write this down
5. Click **Apply**

### Step 4: Verify Internet Connection

1. In the admin panel, go to **INTERNET** on the left sidebar
2. Look at the **Ethernet** section
3. The protocol should be set to **DHCP** (this is the default)
4. You should see a green dot indicating the WAN port has internet
5. If prompted with a captive portal login page, complete the Spectrum
   authentication from within the router's admin panel

**If no internet / no green dot:**
- The Spectrum switch port may require MAC registration
- Note the GL-MT3000's WAN MAC address (found in the admin panel under
  INTERNET or on the bottom label)
- From a device already connected to Spectrum Community Wi-Fi, go to
  https://charter.guestinternet.com and register the GL-MT3000's
  WAN MAC address
- Reboot the GL-MT3000 and check again

### Step 5: Configure Your Private Wi-Fi Network

1. In the admin panel, go to **WIRELESS** on the left sidebar
2. You will see both 2.4GHz and 5GHz bands

**Configure the 5GHz band (recommended for the Mitel adapter):**
- SSID: Choose a name (e.g., "MitelNetwork" or "Office-WiFi")
- Security: **WPA2-PSK** (important - do NOT use WPA3, the Mitel
  adapter does not support it)
- Password: Choose a strong password - write this down, you will
  need it for the Mitel adapter configuration
- Channel: Auto (or manually select a less congested channel)
- Channel Width: 40MHz or 80MHz

**Configure the 2.4GHz band (backup/other devices):**
- SSID: Same name or different (e.g., "MitelNetwork-2G")
- Security: **WPA2-PSK**
- Password: Same or different password

3. Click **Apply** to save settings
4. Your laptop will disconnect - reconnect using the new SSID and password

### Step 6: Verify DHCP Server Settings

1. Go to **MORE SETTINGS > LAN IP** in the admin panel
2. Confirm DHCP is enabled with the default range:
   - Router IP: 192.168.8.1
   - DHCP Range: 192.168.8.100 - 192.168.8.249
3. These defaults are fine - no changes needed

### Step 7: Test Internet

1. While connected to your new SSID, open a browser
2. Navigate to any website (e.g., https://www.google.com)
3. Confirm you have internet access
4. If everything loads, the GL-MT3000 is ready

---

## PHASE 2: Mitel MTL-300AN WLAN Adapter Configuration

### Step 1: Prepare the Adapter

1. Unbox the MTL-300AN
2. Locate the included network cable and AC power adapter
3. Do NOT connect the Mitel phone yet

### Step 2: Enter Configuration Mode

1. Plug the AC adapter into the MTL-300AN and connect to power
2. Wait for the **POWER LED** on top to turn **Red**
3. **Press and hold** the push switch on the front of the adapter
4. Continue holding for approximately 20 seconds
5. Release the push switch when the **WLAN LED** and **STATUS LED**
   start to **blink Green together**
6. The adapter is now in Configuration Mode

### Step 3: Connect Your Laptop to the Adapter

1. **Disconnect your laptop from Wi-Fi** (temporarily)
2. Using the included network cable, connect your laptop's Ethernet
   port directly to the MTL-300AN's Ethernet port
3. Wait a few seconds for the connection to establish
4. The **Link LED** should turn Green

### Step 4: Access the Adapter's Web Interface

1. Open a web browser on your laptop
2. Navigate to: **http://mitel.ca**
3. If that does not load, try the adapter's direct IP:
   - Open Command Prompt on your laptop
   - Type: ipconfig
   - Find your Ethernet adapter's IP address (e.g., 192.168.0.100)
   - Add 1 to the last number (e.g., 192.168.0.101)
   - Enter that address in the browser
4. The MTL-300AN configuration page should appear
5. If a password prompt appears, leave it blank (no default password)
   or try "admin"

### Step 5: Configure the Wi-Fi Connection

1. On the configuration page, look for **Wireless Settings** or
   **Site Survey / Scan**
2. Click **Scan** or **Site Survey** to find available networks
3. Select your GL-MT3000's SSID (the 5GHz SSID you created in Phase 1,
   e.g., "MitelNetwork")
4. Enter the Wi-Fi password you set on the GL-MT3000
5. Set security type to: **WPA2-PSK (AES)** or **WPA2-Personal**
6. Click **Apply** or **Save**

### Step 6: Reboot the Adapter

1. Disconnect the network cable from your laptop
2. Power cycle the MTL-300AN (unplug power, wait 10 seconds, plug back in)
3. Wait for the adapter to boot and connect to your GL-MT3000's Wi-Fi

### Step 7: Verify Connection via LED Indicators

| LED | Expected State | Meaning |
|-----|---------------|---------|
| POWER | Steady Green | Powered on |
| WLAN | Steady Green | Connected to Wi-Fi (Infrastructure mode) |
| STATUS | Steady Green | Connection established |
| Link | Off (for now) | No Ethernet device connected yet |

**If WLAN LED is blinking Red:** Security mismatch - re-enter
configuration mode and verify the SSID password and security type
match exactly what you set on the GL-MT3000.

---

## PHASE 3: Connect the Mitel IP Phone

### Step 1: Cable the Phone

1. Connect an Ethernet cable from the MTL-300AN's Ethernet port to
   the Mitel phone's **LAN/Network port**
2. If the phone needs external power (no PoE from the adapter), connect
   the phone's power supply

### Step 2: Boot the Phone

1. The phone should power on and begin its boot sequence
2. You should see "Discovering DHCP..." on the phone's display
3. The phone should now receive an IP address from the GL-MT3000's
   DHCP server (192.168.8.x range)
4. The MTL-300AN's **Link LED** should turn Green

### Step 3: Verify DHCP Lease

1. If the phone successfully gets past "Discovering DHCP," it received
   an IP address - proceed to Step 4
2. You can verify by logging into the GL-MT3000 admin panel
   (http://192.168.8.1) and checking **CLIENTS** to see the connected
   devices - the Mitel phone should appear

### Step 4: Configure the Phone's Network Mode

If the phone is STILL stuck on "Discovering DHCP" even with the
GL-MT3000 setup:

1. On the Mitel phone, press the **Menu** button
2. Navigate to **Admin Menu** (default password may be blank or "12345")
3. Go to **Network Settings**
4. Verify the phone is set to **SIP** mode, NOT MiNet mode
   - MiNet mode expects a Mitel call server to respond during DHCP
   - SIP mode accepts a standard DHCP lease
5. Verify **VLAN** is set to **Disabled** or **None** (untagged)
   - The GL-MT3000 does not use VLANs
6. Save settings and reboot the phone

### Step 5: SIP Registration

Once the phone has an IP address, it needs to register with your
VoIP/SIP server:

1. Go to the phone's **Admin Menu > SIP Settings**
2. Enter your SIP server address, username, and password
3. The phone should register and show as ready for calls

---

## Troubleshooting

### Phone stuck on "Discovering DHCP"

1. Check the MTL-300AN LEDs:
   - WLAN should be steady Green (connected to GL-MT3000)
   - Link should be steady Green (phone is physically connected)
2. Verify the GL-MT3000 has internet (green dot in admin panel)
3. Check the phone is in SIP mode, not MiNet mode
4. Check VLAN is disabled on the phone
5. Try rebooting in order: GL-MT3000 first, then MTL-300AN, then phone
6. Log into GL-MT3000 admin panel > CLIENTS and check if the
   MTL-300AN appears as a connected wireless client

### MTL-300AN won't connect to GL-MT3000 Wi-Fi

1. Re-enter configuration mode on the MTL-300AN (hold push switch
   until LEDs blink green)
2. Verify SSID name is typed exactly (case-sensitive)
3. Verify password is correct
4. Ensure GL-MT3000 is set to WPA2-PSK, not WPA3
5. Try connecting to the 2.4GHz SSID instead of 5GHz

### GL-MT3000 has no internet

1. Check the Ethernet cable is in the WAN port (2.5G port), not LAN
2. Try a different port on the Spectrum switch
3. Check if the port requires MAC registration at
   https://charter.guestinternet.com
4. Contact property management to verify the switch port is active

### Phone gets IP but won't register/make calls

1. Verify SIP server settings are correct
2. Check if the GL-MT3000 firewall is blocking SIP traffic:
   - Admin panel > FIREWALL > Open Ports on Router
   - Ensure SIP ports are not blocked (UDP 5060, 5061 and
     RTP range 10000-20000)
3. Some VoIP services require specific DNS settings:
   - Admin panel > MORE SETTINGS > Custom DNS Server
   - Try setting DNS to 8.8.8.8 and 8.8.4.4

### Factory Reset Procedures

**GL-MT3000 factory reset:**
- Press and hold the Reset button on the router for 10+ seconds
  while powered on until the LED flashes rapidly, then release

**MTL-300AN factory reset:**
- Press and hold the push switch while powering on the adapter
- Release when the WLAN LED changes from Green to Red
- All settings will be erased

---

## Quick Reference

| Setting | Value |
|---------|-------|
| GL-MT3000 Admin URL | http://192.168.8.1 |
| GL-MT3000 Default SSID | GL-MT3000-xxx (check bottom label) |
| GL-MT3000 Default Wi-Fi Pass | goodlife (check bottom label) |
| GL-MT3000 DHCP Range | 192.168.8.100 - 192.168.8.249 |
| MTL-300AN Config URL | http://mitel.ca |
| MTL-300AN Config Mode | Hold push switch ~20sec until LEDs blink green |
| Recommended Security | WPA2-PSK (AES) |
| Recommended Band | 5GHz |
| Spectrum Device Portal | https://charter.guestinternet.com |

---

## Sources

- GL.iNet GL-MT3000 Documentation: https://docs.gl-inet.com/router/en/4/user_guide/gl-mt3000/
- Mitel WLAN Adapter Setup Guide: https://www.mitel.com/document-center/devices-and-accessories/ip-phones/6900-series/6900-accessories/mitel-wireless-lan-adapter/all-releases/en/mitel-wlan-adapter-setup-guide
- Spectrum Community WiFi Device Management: https://www.spectrum.net/support/spectrum-community-solutions/device-management-spectrum-community-solutions
- Spectrum Browserless Device Connection: https://www.spectrum.net/support/spectrum-community-solutions/connecting-browserless-devices-your-spectrum-community-solutions-network
