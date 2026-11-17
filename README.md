# Zerogotchi

Zerogotchi is a Wi-Fi audit app for the M5Stack Cardputer Zero stock launcher.
Use **F/X** or the arrow keys to move, **Enter** to select, and **Esc** to go back.
**Auto** is reserved for future automatic auditing.

## Install on the Cardputer Zero

From a checkout on the Cardputer, run:

```sh
make package
sudo apt install ./build/zerogotchi_0.3.0_all.deb
```

The stock launcher detects the package's desktop entry in
`/usr/share/APPLaunch/applications/` and adds Zerogotchi to its carousel.
See the [APPLaunch application model](https://github.com/CardputerZero/launcher/blob/master/docs/launcher-project-guide/04-application-model-and-launch-mechanism.md).

## Attack mode

Only test Wi-Fi networks and clients you own.

1. Select **Attack**, accept the disclaimer, and scan for nearby networks.
2. Connect a USB Wi-Fi adapter whose Linux driver supports monitor mode. For
   deauthentication, the adapter must also support frame injection. Keep this
   adapter disconnected from Wi-Fi and set its interface name in **Settings**.
   Run `/usr/sbin/iw dev` over SSH to identify it. The onboard adapter on stock firmware
   can scan but does not advertise monitor mode.
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
   PCAP and uses `hcxpcapngtool` to save target-filtered Hashcat mode 22000
   records when a usable exchange is found.

Each radio session asks for your Cardputer user password with masked input. The app passes it
to sudo through a pipe for that capture. The helper brings down idle managed
interfaces on the dedicated USB radio, opens a temporary monitor interface,
and streams the capture to the app. It then removes the monitor interface and
restores the original interface state. Keep the onboard radio connected for SSH
and uploads.

## OnlineHashCrack

Enter your key in **Settings**. The app masks typed characters and saves the key
in a user-only file at `${XDG_CONFIG_HOME:-~/.config}/zerogotchi/api-key`.

With a key saved, Zerogotchi asks whether to upload after capturing a usable
handshake or PMKID. You can also choose **Submit WPA hashes** on a saved capture. Accept the
[OnlineHashCrack terms](https://www.onlinehashcrack.com/terms-conditions.php)
before sending. The app sends mode 22000 records in batches of up to 50 and
displays accepted, skipped, and rejected totals. PCAPs stay on the Cardputer.
