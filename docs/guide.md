# User guide

[Back to Zerogotchi](../README.md) · [Installation](installation.md)

[Auto](#auto) · [Attack](#attack) · [Saved captures](#saved-captures) ·
[Wardriving](#wardriving) · [Settings](#settings-and-dashboard) ·
[OnlineHashCrack](#onlinehashcrack) · [Private Zones](#private-zones) · [Survey uploads](#survey-uploads)

Use **F/X** or the arrow keys to move, **Enter** to select, and **Esc** to go
back or stop. Use Auto and Attack only on networks and clients you own or are
authorized to test.

Press **P** on an active Auto or Wardriving dashboard to pause, then press
**P** again to resume. Auto saves any packets received before the pause and
retries the same target on resume if it still needs a usable result.
Wardriving finishes an in-progress scan and discards its results if paused.
**Esc** still ends the session while paused.

## Auto

1. Select **Auto → Start sweep** and confirm the scope.
2. With an OnlineHashCrack key configured, review the upload terms and consent.
3. Enter your Cardputer user password. The sweep runs unattended after this.

Auto takes one scan and visits eligible BSSIDs in signal order. Each target gets
up to 25 seconds or until a usable handshake or PMKID is found. The same USB
monitor interface stays up across targets. Auto listens passively on supported
DFS, no-IR, and 6 GHz channels. It gives an access point one short passive retry
if it returns no packets.

Auto skips hidden, open, enterprise, WPA3-only, and unsupported-channel
networks, along with blacklisted SSIDs and BSSIDs with an existing usable
handshake or PMKID. Other APs with the same SSID can still be eligible.

The dashboard reports the current radio phase, visited targets, usable results,
and completed deauth bursts across the session. It idles after the sweep.
Press **Esc** to return. During a sweep, **Esc** closes and saves the current
capture. **Auto → Session log** shows decisions, capture outcomes, and
upload results. Each sweep replaces the previous log.

## Attack

Select **Attack**, accept the scope disclaimer, scan, and select a BSSID.

| Mode | Behavior |
| --- | --- |
| **Passive Capture** | Listens for WPA authentication frames and PMKIDs. |
| **Deauth Attack** | Discovers clients on the selected AP and sends directed bursts until you press **Esc**. |
| **Active Capture** | Captures while directing deauth bursts at observed clients on the selected AP. |

The themed Attack dashboard shows the target, radio phase, elapsed time,
capture size, client counts, deauth results, and handshake or PMKID progress.
Capture modes stop on a usable result or **Esc**.
Clients using Protected Management Frames may ignore injected deauth frames.
Support for individual WPA/WPA2 security variants depends on the observed
exchange and `hcxpcapngtool`.

The app asks for your Cardputer user password in a masked field for privileged
radio access. The helper temporarily brings down idle managed interfaces on
the chosen USB radio and restores them after the session. Keep the onboard
adapter connected if you use it for SSH and uploads.

## Saved captures

Under **Attack → Saved captures**, entries are listed by network name.
Zerogotchi keeps the PCAP and saves usable target-filtered hashes as
`.hc22000`. Earlier `.22000` files are also readable.

**Clear all handshakes** prompts before deleting local entries with usable
hashes and their corresponding PCAPs. PCAP-only entries and remote
OnlineHashCrack tasks stay available.

## Wardriving

1. Select **Wardriving → Start** and enter your Cardputer user password to
   power GPS.
2. Watch completed scans, unique networks, logged GPS observations, and your
   latest latitude and longitude. A stale position is labeled **Last GPS fix**.
3. Press **Esc** to stop. With a saved survey, the summary offers each upload
   service that has credentials configured, along with **Exit to main menu**.
   Find local reports later under **Wardriving → Saved surveys**.

Wardriving repeatedly scans with NetworkManager and respects the SSID
blacklist. With a fresh fix, it adds observations with measured signal levels
to one local WiGLE CSV per session. This scanning-only mode has its own
adapter setting and defaults to the onboard `wlan0`.

## Settings and dashboard

| Setting | Purpose |
| --- | --- |
| **Theme** | Choose the default Acidburn portraits, the animated Computer face, or Zero Cool. The setting applies to Auto and Wardriving. |
| **WiFi Adapter** | Set the radio for Auto and Attack. |
| **Wardriving Adapter** | Choose the Wardriving interface independently. |
| **SSID blacklist** | Enter case-sensitive, comma-separated SSIDs. Quote a name that contains commas. With a blacklist enabled, hidden SSIDs are excluded when their names cannot be checked. |
| **Private Zones** | Add GPS circles where survey recording pauses. |
| **OnlineHashCrack API key** | Save a masked key for WPA hash submissions. |
| **WiGLE** | Save and test the WiGLE API name and token, and manage survey uploads. |
| **WDG Wars** | Save and test the WDG Wars API key, and manage survey uploads. |

The satellite indicator lights after valid GNSS data arrives, the globe reflects
NetworkManager's internet check, and the battery fill uses the stock gauge.
The satellite indicator can light before a fix is available. When the app
powers the stock external 5 V rail for GNSS, it restores the rail when you
leave the mode.

## OnlineHashCrack

After setting a key, choose **Submit WPA hashes** in a saved capture or
respond to the prompt when Attack saves a usable result. Accept the
[service terms](https://www.onlinehashcrack.com/terms-conditions.php) before
sending. With upfront consent, Auto submits new results during its sweep
and retries temporary connection failures while idling.

The app sends mode 22000 hashes in batches of up to 50 and shows accepted,
skipped, and rejected totals. PCAPs stay on the Cardputer.

## Private Zones

1. Open **Settings → Private Zones → Add zone**.
2. Enter the circle center as latitude and longitude in decimal degrees,
   followed by the radius in meters. For example, `40.0,-74.0,150` creates a
   150-meter circle. Use negative values for south and west.
3. Press **Enter** to save. Select a saved circle to edit or remove it.

With a zone configured, Wardriving waits for a fresh GPS fix before scanning.
It displays **Private** and pauses scans when a circle overlaps the estimated
GPS accuracy area. It resumes after a fresh fix places you outside the zones.
Auto applies the circles to its location-tagged survey snapshots.

Both upload services recheck the current zones and the existing SSID blacklist
before sending a saved survey. This filters matching rows out of the upload
copy while keeping the original local CSV. GPS and radio measurements are
estimates. Allow extra radius at the edge of an area you want to protect.

## Survey uploads

In **Settings → WiGLE**, save the API name and token and select **Test
credentials** to verify account access. With credentials configured, Auto can
save one local survey after a fresh GPS fix. Wardriving saves local observations
regardless of whether credentials are configured.

In **Settings → WDG Wars**, choose **Set API key** and enter the 64-character key
from your WDG Wars account. **Test API key** verifies access through the profile
endpoint. The app stores the key in an owner-only settings file.

Choose a destination when Wardriving ends or select a file under **Saved surveys**.
Confirm to send the filtered BSSIDs, SSIDs, signal levels, and GPS coordinates.
WDG Wars uses its CSV upload endpoint. Survey accuracy is estimated from the
receiver’s HDOP. WPA hash submissions use the separate OnlineHashCrack flow.

## Local files

| Data | Default location |
| --- | --- |
| Captures and hashes | `~/.local/share/zerogotchi/captures/` |
| WiGLE surveys | `~/.local/share/zerogotchi/wigle/` |
| Latest Auto session log | `~/.local/share/zerogotchi/auto-session.log` |
| Settings and owner-only credentials | `~/.config/zerogotchi/` |

The app honors `XDG_DATA_HOME` and `XDG_CONFIG_HOME` when set.
