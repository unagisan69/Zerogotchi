<p align="center">
  <img src="app/themes/acidburn/neutral.png" width="112" height="112" alt="Zerogotchi Acidburn portrait">
</p>

<h1 align="center">Zerogotchi</h1>

<p align="center">
  Wi-Fi lab auditing and GPS-tagged wardriving on the M5Stack Cardputer Zero.<br>
  Opens from the stock launcher.
</p>

<p align="center">
  <a href="https://github.com/unagisan69/Zerogotchi/releases/latest"><img src="https://img.shields.io/github/v/release/unagisan69/Zerogotchi?label=Download&amp;color=237965" alt="Latest release"></a>
</p>

<p align="center">
  <a href="#quick-install">Quick install</a> ·
  <a href="#features">Features</a> ·
  <a href="docs/guide.md">User guide</a> ·
  <a href="docs/companion.md">wifit3 companion</a>
</p>

| Auto · Acidburn | Wardriving · Zero Cool |
| :--: | :--: |
| ![Auto dashboard with Acidburn and sample sweep counts](docs/images/auto-acidburn.png) | ![Wardriving dashboard with Zero Cool and sample GPS coordinates](docs/images/wardriving-zero-cool.png) |

*Both screens use sample data.*

## Quick install

Run these commands in a terminal on the Cardputer Zero or over SSH.

```sh
curl -fL https://github.com/unagisan69/Zerogotchi/releases/latest/download/zerogotchi_all.deb -o zerogotchi.deb
sudo apt install ./zerogotchi.deb
```

Open **Zerogotchi** in the stock launcher. Use **F/X** or the arrow keys to move, **Enter** to select, and **Esc** to go back or stop a session.

See [installation and troubleshooting](docs/installation.md) for hardware, checksum verification, upgrades, and building from source.

## Features

| Feature | What it does |
| --- | --- |
| **[Auto WiFi Attack](docs/guide.md#auto-wifi-attack)** | Visits eligible access points once, runs capture and directed deauth where the radio and channel allow it, then idles. Skips blocked SSIDs and BSSIDs with saved results. Shows a session-wide deauth count and logs why targets were skipped. |
| **[Manual WiFi Attack](docs/guide.md#manual-wifi-attack)** | Select an access point for Active Capture, Passive Capture, or Deauth Attack. Follow target and radio activity on the themed dashboard. Capture stops on a usable handshake or PMKID. |
| **[Saved captures](docs/guide.md#saved-captures)** | Review captures by network name. Keeps PCAPs and target-filtered Hashcat mode 22000 files. Includes a confirmed **Clear all handshakes** action. |
| **[Wardriving](docs/guide.md#wardriving)** | Scans with a separately configurable adapter and shows unique networks, scan counts, GPS observation counts, and the latest fix. Saves a local WiGLE CSV when GPS has a fix. |
| **[OnlineHashCrack](docs/guide.md#onlinehashcrack)** | Submit WPA hashes from Saved captures, or let Auto send new results after upfront consent. Auto retries temporary connection failures. |
| **[Survey uploads](docs/guide.md#survey-uploads)** | Send GPS-tagged surveys to WiGLE or WDG Wars after Wardriving, or pick a saved survey later. |
| **[Private Zones](docs/guide.md#private-zones)** | Pause Wardriving in GPS circles and filter observations against those zones and the SSID block list before sending. |
| **[Themes and status](docs/guide.md#settings-and-dashboard)** | Choose Acidburn, Computer, or Zero Cool for Auto WiFi Attack, Manual WiFi Attack, and Wardriving. Dashboard indicators use the Cardputer’s live status. |
| **[In-app updates](docs/installation.md#install-or-upgrade)** | Check GitHub for a newer release and install its verified Debian package from the main menu. |

The **built-in Wi-Fi adapter** supports scanning and Wardriving on the stock driver. **Capture and deauth** require a USB adapter that supports monitor mode and, for deauth, injection. **GPS-tagged surveys** require a GNSS HAT with a position fix.

Use Auto and Manual WiFi Attack only on networks and equipment you own or are authorized to test. The optional [wifit3 companion](docs/companion.md) provides additional lab methods, including active PMKID association, WPS recovery, WEP, and experimental WPA3 downgrade.

Zerogotchi and its bundled theme artwork are available under the [MIT License](LICENSE).
