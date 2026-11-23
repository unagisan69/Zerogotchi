"""Bounded hardware and configuration checks for the device owner."""

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import gnss
import updater
import subghz
import upload_queue
import wifi


def command(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, errors="replace", timeout=5,
                                env={**os.environ, "LC_ALL": "C"})
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def snapshot():
    results = []
    try:
        version = updater.installed_version()
    except wifi.WifiError:
        version = "unknown"
    results.append(("Version", version))
    for label, setting, default in (("WiFi Adapter", "adapter", "unset"),
                                    ("Wardriving Adapter", "wardriving-adapter", "wlan0")):
        interface = wifi.read_setting(setting) or default
        path = Path("/sys/class/net") / interface
        info = command(["/usr/sbin/iw", "dev", interface, "info"]) if path.exists() else ""
        state = "present" if info else "unavailable"
        results.append((label, f"{interface}: {state}"))
        if info:
            phy = next((line.split()[-1] for line in info.splitlines() if line.strip().startswith("wiphy ")), None)
            modes = command(["/usr/sbin/iw", "phy", f"phy{phy}", "info"]) if phy else ""
            results.append((f"{interface} monitor mode", "supported" if "* monitor" in modes else "unavailable"))
    bt = command(["bluetoothctl", "show"])
    results.append(("Bluetooth", "powered on" if "Powered: yes" in bt else "off or unavailable"))
    results.append(("Internet", "online" if upload_queue.online() else "offline or limited"))
    results.append(("GPS source", gnss.SOURCES[gnss.source_key()]))
    monitor = gnss.GnssMonitor(wifi.HELPER, "", enable_power=False)
    receiving = False
    try:
        until = time.monotonic() + 1.5
        while monitor.fd is not None and time.monotonic() < until:
            receiving |= monitor.poll(time.monotonic())
            time.sleep(0.1)
        now = time.monotonic()
        results.append(("GNSS", "receiving NMEA" if receiving else monitor.error or "no recent NMEA; start a session to power the selected GPS"))
        satellites = monitor.satellites(now)
        results.append(("GPS satellites", str(satellites) if satellites is not None else "unknown"))
        results.append(("GPS fix", "current" if monitor.position(now) else "waiting"))
    finally:
        monitor.close()
    try:
        profile = subghz.HATS[subghz.hat_key()]
        results.append(("CC1101 profile", profile.name))
        results.append(("CC1101 frequency", f"{subghz.configured_frequency():g} MHz"))
    except wifi.WifiError as error:
        results.append(("CC1101 config", str(error)))
    results.append(("LoRa interface", "SPI available" if os.access("/dev/spidev0.1", os.R_OK | os.W_OK) else "unavailable"))
    backlight = Path("/sys/class/backlight/backlight/brightness")
    results.append(("Display control", "available" if os.access(backlight, os.W_OK) else "unavailable"))
    try:
        settings = json.loads((Path.home() / ".config/cardputerzero/config.json").read_text())
        dark_time = int(settings.get("dark_time", 30))
        results.append(("DarkTime", "Never" if dark_time == 0 else f"{dark_time} seconds"))
    except (OSError, ValueError, TypeError, AttributeError):
        results.append(("DarkTime", "default 30 seconds"))
    missing = [tool for tool in ("nmcli", "iw", "bluetoothctl", "curl", "tcpdump", "hcxpcapngtool", "pinctrl")
               if not shutil.which(tool, path=os.environ.get("PATH", "") + ":/usr/sbin")]
    results.append(("Tools", ", ".join(missing) + " missing" if missing else "all installed"))
    free = shutil.disk_usage(wifi.DATA if wifi.DATA.exists() else Path.home()).free
    results.append(("Free storage", f"{free / 1048576:.0f} MiB"))
    for label, setting, parse in (("SSID block list", "ssid-blacklist", wifi.parse_blacklist),
                                  ("SSID allow list", "ssid-allow-list", wifi.parse_blacklist)):
        try:
            detail = f"{len(parse(wifi.read_setting(setting)))} entries"
        except csv.Error:
            detail = "invalid; edit in Settings"
        results.append((label, detail))
    from wigle import credentials as wigle_credentials
    from wdgwars import credentials as wdg_credentials
    for name, configured in (("WiGLE", wigle_credentials()), ("WDG Wars", wdg_credentials()),
                              ("OnlineHashCrack", wifi.KEY_PATTERN.fullmatch(wifi.read_setting("api-key")))):
        results.append((name, "credentials saved" if configured else "not configured"))
    counts = {}
    for job in upload_queue.jobs():
        counts[job["state"]] = counts.get(job["state"], 0) + 1
    results.append(("Upload queue", ", ".join(f"{count} {state}" for state, count in counts.items()) or "empty"))
    return results
