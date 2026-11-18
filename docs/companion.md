# wifit3 companion

[Back to Zerogotchi](../README.md) · [Installation](installation.md)

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
