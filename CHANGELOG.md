# Changelog

This file tracks user-visible changes to Zerogotchi's GitHub releases.
For setup and controls, see the [user guide](docs/guide.md) and
[installation instructions](docs/installation.md).

When adding a user-facing feature, update the README and the relevant guide,
then record the change under **Unreleased**. Move those entries under the
version heading when publishing a release.

## Unreleased

## [0.4.20](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.20)

- Added Diagnostics for radio interfaces, GPS, connectivity, storage, tools, and configuration.
- Fixed internet detection to read NetworkManager connectivity without requiring privileged rechecks.
- Follow APPLaunch DarkTime, including Never. The first keypress wakes the display while collection continues.
- Added a persistent Upload queue for WiGLE, WDG Wars, MeshCore, and consented OnlineHashCrack submissions. Pending jobs retry while the app is open and online, with separate provider outcomes and manual retry/cancel controls.

## [0.4.19](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.19)

- Clarified WiGLE credential setup with numbered steps, a Next action, a visible API Name, saved status, and automatic account verification.
- Added a quiet background update check on launch, with Update selected by default.
- Updates show download and installation progress and restart the app after installation, following meshcore-zero's update flow.

## [0.4.18](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.18)

- Use the canonical 16-character MeshCore ID for WDG Wars and show merge and rejection details.
- Open saved Meshmapping surveys to retry a WDG Wars export.

## [0.4.17](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.17)

- Added receive-only **Meshmapping** for the Cap LoRa-1262, with unique MeshCore nodes, GPS status, local surveys, and optional WDG Wars export.

## [0.4.16](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.16)

- Put **Upload to All** first in Wardriving export menus and preselect **Upload** on the confirmation screen.

## [0.4.15](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.15)

- Scan Wi-Fi and Bluetooth LE concurrently and show Wi-Fi detections separately from newly logged addresses.
- Use WDG Wars’ signed upload API for Wi-Fi and BLE and show any rows without an import result.
- Offer **Upload to both** when WiGLE and WDG Wars credentials are saved, with an outcome for each service.

## [0.4.14](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.14)

- Added an SSID allow list for Auto WiFi Attack and Wardriving.
- Added lifetime results and a counter reset for Auto, Manual, and Wardriving.
- Wardriving shows satellites in view, logs Bluetooth LE observations, and skips previously logged Wi-Fi and BLE addresses across sessions.

## [0.4.13](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.13)

- Renamed the two attack modes to **Auto WiFi Attack** and **Manual WiFi Attack**.
- Renamed the setting to **SSID block list**. Existing entries carry over.

## [0.4.12](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.12)

- Added **P** to pause or resume Auto and Wardriving. **Esc** ends either session.
- Auto saves the current packets when paused and returns to the same access point if it still needs a usable result. Wardriving starts a fresh scan on resume.

## [0.4.11](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.11)

- Added WDG Wars API key setup and a read-only account check. Wardriving offers configured destinations for WiGLE and WDG Wars uploads.
- Added **Private Zones** to pause Wardriving scans inside configured GPS circles or while waiting for a fresh fix. Both upload services apply the current zones and SSID block list to saved surveys before sending.

## [0.4.10](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.10)

- Added MIT licensing for the app and artwork, Cardputer Zero AppStore listing metadata, and a real-device review demo.

## [0.4.9](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.9)

- Added CardputerZero AppBuilder packaging for the Python framebuffer app. The AppBuilder and local builds share package metadata, assets, and the stock launcher entry.

## [0.4.8](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.8)

- Added a WiGLE upload prompt at the end of Wardriving when a survey and credentials are available.
- Put Active Capture first and added a themed dashboard showing live attack and capture activity.
- Added an in-app GitHub release updater. Updated About with current features and author credit.

## [0.4.7](https://github.com/unagisan69/Zerogotchi/releases/tag/v0.4.7)

- First packaged GitHub release. Includes Auto and targeted attack modes, GPS-tagged Wardriving with WiGLE CSV export, and the Acidburn, Computer, and Zero Cool themes.
- Wardriving uses a separate adapter setting that defaults to the onboard Wi-Fi radio. Dashboard GPS, internet, and battery icons share a common height.
