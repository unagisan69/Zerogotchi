# Zerogotchi

Zerogotchi is a Wi-Fi audit app for the M5Stack Cardputer Zero stock launcher.
Use **F/X** or the arrow keys to move, **Enter** to select, and **Esc** to go back.

## Install on the Cardputer Zero

On the Cardputer, install the build tools and runtime dependencies, then build
and install the Debian package:

```sh
sudo apt update
sudo apt install -y git make dpkg-dev python3 python3-pil fonts-dejavu-core \
  network-manager sudo iw iproute2 tcpdump aircrack-ng hcxtools curl ca-certificates
git clone https://github.com/unagisan69/Zerogotchi.git
cd Zerogotchi
make package
sudo apt install ./build/zerogotchi_0.4.5_all.deb
```

The stock launcher detects the package's desktop entry in
`/usr/share/APPLaunch/applications/` and adds Zerogotchi to its carousel.
See the [APPLaunch application model](https://github.com/CardputerZero/launcher/blob/master/docs/launcher-project-guide/04-application-model-and-launch-mechanism.md).

## Auto and the SSID blacklist

In **Settings → SSID blacklist**, enter comma-separated network names.
Matching is case-sensitive. Quote names that contain commas. With a blacklist
set, hidden networks are also excluded since their names cannot be checked.
The exclusions apply to both Attack and Auto.

Use **Auto** only where you own all the networks it can target. It scans once
and visits each eligible BSSID in signal order, using Active Capture for up
to 25 seconds per target or until it finds a usable handshake or PMKID.
Auto skips hidden, open, enterprise, WPA3-only, and unsupported-channel
networks, and APs with a saved usable handshake or PMKID for the same BSSID.
It leaves the USB adapter in monitor mode for the sweep and uses
passive capture on supported channels marked DFS or no-IR, or on 6 GHz.
If an access point yields no packets, Auto gives it one short passive retry.
If monitor startup fails on the Realtek RTL8821CU USB device `0bda:c811`,
Zerogotchi resets that USB device once and retries. If startup still fails,
reconnect the capture adapter and restart Auto.
It asks for authorization and your Cardputer password before
starting. With an OnlineHashCrack key configured, it also asks you to accept
the service terms up front, then automatically submits new hash files and
retries temporary connection failures while idling. Auto always saves results
locally for review in **Saved captures**. It idles with the sweep totals until
you press **Esc**. During a sweep, **Esc** stops after the current capture is
closed and saved.

The dashboard shows the current radio phase, completed deauth bursts across
the session, and a combined count of usable handshakes and PMKIDs. Its satellite
icon lights when the HAT sends valid NMEA at 115200 baud. The globe indicates
whether NetworkManager's internet check passes, and the battery icon shows the
stock gauge's charge percentage. Auto restores the external 5 V rail when you
leave if it powered the rail on.

**Settings → Theme** switches the Auto dashboard face between **Acidburn**
(the default illustrated theme) and **Computer** (the original animated face).

**Auto → Session log** shows the latest sweep's decisions and results. The file
at `${XDG_DATA_HOME:-~/.local/share}/zerogotchi/auto-session.log` is replaced
when a new sweep starts.

## WiGLE

In **Settings → WiGLE**, enter the API name and token, then select **Test
credentials** to check the account without uploading a report. With credentials
configured, Auto records a local WiGLE CSV after a fresh GNSS position fix
and an onboard Wi-Fi scan. The survey uses measured signal levels and estimates
position accuracy from the receiver's HDOP. It excludes SSIDs on the blacklist.

To report the survey, open **Settings → WiGLE → Saved surveys**, choose a file,
and confirm the upload. A survey includes BSSIDs, SSIDs, signal levels, and
observation coordinates. WPA captures and hashes are handled separately.

## Attack mode

Only test Wi-Fi networks and clients you own.

1. Select **Attack**, accept the disclaimer, and scan for nearby networks.
2. In **Settings → WiFi Adapter**, select **Built-in (wlan0)** for scans,
   **wlan1** for the usual USB capture adapter, or **Custom** for a different
   interface. Run `/usr/sbin/iw dev` over SSH to identify your interface.
   The stock onboard driver lacks monitor mode. Capture requires a
   disconnected USB adapter with monitor mode, and deauth also requires
   frame injection.
3. Select the target BSSID and choose a mode:

   - **Passive Capture** listens for authentication exchanges.
   - **Deauth Attack** repeatedly sends directed deauthentication bursts to
     recently observed clients of that BSSID, until you press **Esc**.
   - **Active Capture** runs directed deauthentication and capture together.

   Capture modes show live progress and stop when a usable handshake or PMKID
   is detected, or when you press **Esc**. Clients using Protected Management
   Frames may ignore injected deauth frames.

4. **Saved captures** shows each network's SSID. Files are stored in
   `${XDG_DATA_HOME:-~/.local/share}/zerogotchi/captures/`. Zerogotchi keeps the
   PCAP and uses `hcxpcapngtool` to save target-filtered `.hc22000` files
   when a usable exchange is found. Older `.22000` captures remain readable.
   **Clear all handshakes** deletes local entries containing usable hashes,
   including the corresponding PCAPs. PCAP-only entries and OnlineHashCrack
   tasks stay available.

Each radio session asks for your Cardputer user password with masked input. The app passes it
to sudo through a pipe for that capture. The helper brings down idle managed
interfaces on the dedicated USB radio, opens a temporary monitor interface,
and streams the capture to the app. It then removes the monitor interface and
restores the original interface state. Keep the onboard radio connected for SSH
and uploads.

## Advanced methods with the wifit3 companion

Zerogotchi's native modes cover passive WPA/WPA2 capture, directed deauth,
and passive PMKID capture. The exact authentication variants that produce a
usable hash depend on the captured exchange and `hcxpcapngtool`. The
experimental WPA3 downgrade, active PMKID association, WPS recovery, and WEP
methods use the separate [wifit3 terminal application](https://github.com/derv82/wifit3).
Its upstream feature list covers CSA/BTM/deauth-based Evil Twin, WPS
PixieDust/PBC/PIN recovery, and WEP ARP replay, ChopChop, fake authentication,
and PTW. Availability varies by AP, client, and adapter.

Install the pinned ARM64 release as your normal user on the Cardputer:

```sh
sudo apt install -y curl ca-certificates libusb-1.0-0
install -d -m755 ~/.local/bin
download=$(mktemp)
curl -fL https://github.com/derv82/wifit3/releases/download/v0.7.1/wifit3-linux-arm64 -o "$download" &&
  printf '%s  %s\n' 7cb0447c21dd7ad4d9c904d436da78cb1b1054f8ff8fb822aeb3793415df7fcd "$download" |
  sha256sum -c - &&
  install -m755 "$download" ~/.local/bin/wifit3
rm -f "$download"
~/.local/bin/wifit3
```

Launch it over SSH in a terminal. Its adapter picker runs without changing
drivers. Starting its first USB session may ask for administrator access to
configure udev permissions and module blocklists. Use a separate supported
USB adapter if you want to keep your Zerogotchi capture adapter on its stock
kernel driver, or follow wifit3's uninstall procedure before switching it
back. Companion sessions keep their own captures and do not read the
Zerogotchi blacklist or upload settings. Check the
[upstream hardware matrix](https://github.com/derv82/wifit3/blob/master/docs/SUPPORTED-HARDWARE.md)
for per-chipset support.

## OnlineHashCrack

Enter your key in **Settings**. The app masks typed characters and saves the key
in a user-only file at `${XDG_CONFIG_HOME:-~/.config}/zerogotchi/api-key`.

With a key saved, Zerogotchi asks whether to upload after capturing a usable
handshake or PMKID. You can also choose **Submit WPA hashes** on a saved capture. Accept the
[OnlineHashCrack terms](https://www.onlinehashcrack.com/terms-conditions.php)
before sending. The app sends mode 22000 records in batches of up to 50 and
displays accepted, skipped, and rejected totals. PCAPs stay on the Cardputer.
