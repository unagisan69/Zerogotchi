# Installation

[Back to Zerogotchi](../README.md)

## Install or upgrade

Use the [latest GitHub release](https://github.com/unagisan69/Zerogotchi/releases/latest) on a Cardputer Zero running the stock M5Stack Debian image.

```sh
curl -fL https://github.com/unagisan69/Zerogotchi/releases/latest/download/zerogotchi_all.deb -o zerogotchi.deb
sudo apt install ./zerogotchi.deb
```

The package declares its runtime dependencies, which APT resolves.
Use these same commands for upgrades. Installed packages and user data live
in separate directories, so upgrades preserve settings and captures.
You can also open **Update** on the main menu. It checks the latest GitHub
release, verifies the Debian package and checksum, and asks for your Cardputer
password before installation. Reopen Zerogotchi from the launcher when it finishes.

The stock launcher reads the installed desktop entry from
`/usr/share/APPLaunch/applications/` and adds a **Zerogotchi** tile.
See the [APPLaunch application model](https://github.com/CardputerZero/launcher/blob/master/docs/launcher-project-guide/04-application-model-and-launch-mechanism.md).

### Verify the download

Download the package and checksum from the same release tag. Set `tag` to
the version shown on the release page.

```sh
tag=v0.4.16
curl -fLO "https://github.com/unagisan69/Zerogotchi/releases/download/$tag/zerogotchi_all.deb"
curl -fLO "https://github.com/unagisan69/Zerogotchi/releases/download/$tag/SHA256SUMS.txt"
sha256sum -c SHA256SUMS.txt
sudo apt install ./zerogotchi_all.deb
```

## Choose an adapter

| Task | Adapter |
| --- | --- |
| Scan for networks | Built-in Wi-Fi (`wlan0`) or a compatible managed Wi-Fi adapter |
| Wardriving | Built-in Wi-Fi and Bluetooth LE. Change the Wi-Fi interface under **Settings → Wardriving Adapter**. |
| Passive capture, Active Capture, and Auto | A disconnected USB Wi-Fi adapter with monitor mode. Active Capture and Auto deauth also require injection. |

Use `/usr/sbin/iw dev` to identify interface names. In **Settings → WiFi Adapter**,
select **wlan1** for the usual USB adapter or **Custom** to enter a different
interface. The installed stock driver for the built-in Wi-Fi adapter supports
scanning and lacks monitor mode.

For GPS-tagged observations, attach the stock-compatible GNSS HAT. A lit
satellite icon means the HAT is sending valid NMEA. Wardriving displays a
position when the receiver has obtained a fix.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Auto skips many access points | Open **Auto WiFi Attack → Session log** for each skip reason. Saved hashes, hidden SSIDs, block-list entries, incompatible security, and unsupported channels all affect eligibility. |
| Monitor mode fails to start | Check that the selected USB adapter is disconnected from Wi-Fi. Zerogotchi attempts one reset for the supported RTL8821CU device `0bda:c811`. If that fails, reconnect the adapter and retry. |
| Satellite icon is lit, but coordinates are blank | The HAT is communicating. Give the receiver time and a clear view of the sky to acquire a fix. |
| A survey is absent from WiGLE | Select it under **Wardriving → Saved surveys**, confirm upload, and check WiGLE’s upload history. Accepted uploads may wait for processing. |

## Build from source

```sh
sudo apt update
sudo apt install -y git make
git clone https://github.com/unagisan69/Zerogotchi.git
cd Zerogotchi
make package
sudo apt install ./build/zerogotchi_*_all.deb
```

Build from a fresh checkout, or run `make clean` first if the build directory
contains packages from earlier versions. Remove the installed package with
`sudo apt remove zerogotchi`.

### Build with CardputerZero AppBuilder

Zerogotchi follows the [AppBuilder Python framebuffer example](https://github.com/CardputerZero/AppBuilder/tree/main/examples/Python_FrameBuffer_HelloWorld).
From the Zerogotchi checkout on Linux:

```sh
git clone https://github.com/CardputerZero/AppBuilder.git ../AppBuilder
make appbuilder-package APPBUILDER=../AppBuilder
sudo apt install ./build/appbuilder/zerogotchi_*_all.deb
```

Both package commands share the same metadata, staging hook, icon, and desktop
template. The launcher starts the foreground app via
`/usr/share/APPLaunch/bin/zerogotchi`. Python modules and theme assets live in
`/usr/lib/zerogotchi/`, and the package declares its Debian runtime dependencies.

Use AppBuilder's `scripts/pack-deb.sh` path for this project. The generic
**Build DEB Package** workflow targets SCons projects and its packer produces
a different package layout. The root `app-builder.json` supplies listing metadata to `czdev publish`.
First releases require a video of the app running on the device and maintainer review.
