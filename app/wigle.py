"""Local WiGLE surveys and account API, independent of WPA captures."""

import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from dataclasses import dataclass
import uuid

import ble
import lifetime
import wifi
import gnss
import survey_privacy


BASE_URL = "https://api.wigle.net/api/v2"
SURVEYS = wifi.DATA / "wigle"
HEADER = ("MAC", "SSID", "AuthMode", "FirstSeen", "Channel", "Frequency", "RSSI",
          "CurrentLatitude", "CurrentLongitude", "AltitudeMeters", "AccuracyMeters",
          "RCOIs", "MfgrId", "Type")
CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{1,128}")


def credentials():
    name, separator, token = wifi.read_setting("wigle-credentials").partition(":")
    if separator and CREDENTIAL.fullmatch(name) and CREDENTIAL.fullmatch(token):
        return name, token
    return None


def save_credentials(name, token):
    if not CREDENTIAL.fullmatch(name) or not CREDENTIAL.fullmatch(token):
        raise wifi.WifiError("Invalid WiGLE credentials")
    wifi.save_setting("wigle-credentials", f"{name}:{token}")


def request(path, survey=None):
    pair = credentials()
    if not pair:
        raise wifi.WifiError("Save your WiGLE API credentials first")
    # The Cardputer's IPv6 route to WiGLE has reset TLS connections. Use IPv4
    # and keep credentials in curl's stdin config, outside the process arguments.
    args = ["curl", "--disable", "--ipv4", "--silent", "--show-error", "--max-time", "15",
            "--proto", "=https", "--basic", "--header", "Accept: application/json",
            "--config", "-", "--write-out", "\n%{http_code}"]
    if survey:
        filename = str(survey).replace("\\", "\\\\").replace('"', '\\"')
        args += ["--form", f'file=@"{filename}";type=text/csv']
    try:
        response = subprocess.run([*args, BASE_URL + path], text=True, capture_output=True,
                                  input=f'user = "{pair[0]}:{pair[1]}"\n', timeout=17)
    except (OSError, subprocess.SubprocessError) as error:
        raise wifi.WifiError("WiGLE connection failed") from error
    if response.returncode:
        raise wifi.WifiError("WiGLE connection failed")
    payload, _, code = response.stdout.rpartition("\n")
    if not code.isdecimal() or not 200 <= int(code) < 300:
        raise wifi.WifiError(f"WiGLE returned HTTP {code}" if code.isdecimal()
                             else "WiGLE returned an unreadable response")
    try:
        result = json.loads(payload)
    except ValueError as error:
        raise wifi.WifiError("WiGLE returned an unreadable response") from error
    if not isinstance(result, dict) or str(result.get("success")).lower() != "true":
        raise wifi.WifiError("WiGLE did not accept the request")
    return result

def test_profile():
    request("/profile/user")
    return "WiGLE credentials verified."


def surveys():
    return sorted(SURVEYS.glob("survey-*.csv"), reverse=True)


@contextmanager
def export_survey(path):
    if (path.parent != SURVEYS or not re.fullmatch(r"survey-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}\.csv", path.name)
            or path.is_symlink() or not path.is_file()):
        raise wifi.WifiError("Invalid survey file")
    configured = survey_privacy.zones()
    ignored = wifi.blacklist()
    with tempfile.TemporaryDirectory(prefix="zg-survey-") as temp:
        export = Path(temp) / path.name
        count = 0
        try:
            with (path.open(encoding="utf-8", newline="") as source,
                  export.open("w", encoding="utf-8", newline="") as output):
                reader = csv.reader(source, strict=True)
                metadata, columns = next(reader), next(reader)
                if not metadata or metadata[0] != "WigleWifi-1.6" or columns != list(HEADER):
                    raise wifi.WifiError("Unrecognized survey format")
                writer = csv.writer(output)
                writer.writerows((metadata, columns))
                for row in reader:
                    if len(row) != len(HEADER):
                        continue
                    if row[-1] == "WIFI" and (row[1] in ignored or (ignored and not row[1])):
                        continue
                    try:
                        latitude, longitude, accuracy = map(float, (row[7], row[8], row[10]))
                    except ValueError:
                        continue
                    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180
                            and math.isfinite(accuracy) and accuracy >= 0):
                        continue
                    point = gnss.Fix(latitude, longitude, 0, accuracy, 0)
                    if survey_privacy.private(point, configured):
                        continue
                    writer.writerow(row)
                    count += 1
        except (StopIteration, UnicodeError, csv.Error) as error:
            raise wifi.WifiError("Unrecognized survey format") from error
        if not count:
            raise wifi.WifiError("No observations remain after privacy filtering")
        yield export


def upload(path):
    with export_survey(path) as export:
        request("/file/upload", export)
    return "WiGLE accepted the survey for processing."


def rssi_by_bssid(interface="wlan0"):
    try:
        output = subprocess.check_output(["/usr/sbin/iw", "dev", interface, "scan", "dump"],
                                         text=True, errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise wifi.WifiError("WiGLE could not read measured signal levels") from error
    signals = {}
    for section in re.split(r"(?m)^BSS ", output)[1:]:
        address = section[:17].upper()
        freq = re.search(r"(?m)^\s*freq:\s*(\d+)", section)
        signal = re.search(r"(?m)^\s*signal:\s*(-?\d+(?:\.\d+)?) dBm", section)
        if wifi.MAC_PATTERN.fullmatch(address) and freq and signal:
            signals[address, int(freq[1])] = round(float(signal[1]))
    return signals


def channel(freq):
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq < 5925:
        return (freq - 5000) // 5
    if freq == 5935:
        return 2
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


def capabilities(security):
    modes = security.split()
    if "802.1X" in modes:
        methods = [f"[{mode}-EAP]" for mode in ("WPA1", "WPA2", "WPA3") if mode in modes]
        return ("".join(methods) or "[EAP]") + "[ESS]"
    labels = {"WPA1": "[WPA-PSK]", "WPA": "[WPA-PSK]", "WPA2": "[WPA2-PSK]",
              "WPA3": "[RSN-SAE]", "OWE": "[RSN-OWE]", "WEP": "[WEP]"}
    return "".join(labels.get(mode, "") for mode in modes) + "[ESS]"


def survey_rows(networks, signals, fix, now):
    rows = []
    for network in networks:
        signal = signals.get((network.bssid, network.frequency))
        band_channel = channel(network.frequency)
        if signal is not None and band_channel is not None:
            rows.append((network.bssid, network.ssid, capabilities(network.security),
                         now.strftime("%Y-%m-%d %H:%M:%S"), band_channel, network.frequency, signal,
                         f"{fix.latitude:.7f}", f"{fix.longitude:.7f}", f"{fix.altitude:.1f}",
                         f"{fix.accuracy:.1f}", "", "", "WIFI"))
    return rows


def append_survey(path, rows, now):
    if path is not None:
        with path.open("a", encoding="utf-8", newline="") as output:
            csv.writer(output).writerows(rows)
        return path
    SURVEYS.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=SURVEYS,
                                     prefix=".survey-", delete=False) as output:
        temporary = Path(output.name)
        try:
            writer = csv.writer(output)
            writer.writerow(("WigleWifi-1.6", "appRelease=Zerogotchi", "model=Cardputer Zero"))
            writer.writerow(HEADER)
            writer.writerows(rows)
            output.flush()
            destination = SURVEYS / f"survey-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.csv"
            os.replace(temporary, destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)


def record_survey(position, cancel, pause=None):
    configured = survey_privacy.zones()
    def interrupted():
        return cancel.is_set() or (pause is not None and pause.is_set())

    fix = position()
    if interrupted() or fix is None or survey_privacy.private(fix, configured):
        return None
    networks = wifi.scan(interface="wlan0", allowed=wifi.allowlist())
    signals = rssi_by_bssid()
    fix = position()
    if fix is None or interrupted() or survey_privacy.private(fix, configured):
        return None
    now = datetime.now(timezone.utc)
    rows = survey_rows(networks, signals, fix, now)
    if rows and not interrupted():
        return append_survey(None, rows, now)
    return None


@dataclass(frozen=True)
class WardriveStatus:
    phase: str
    scans: int = 0
    networks: int = 0
    observations: int = 0
    error: str = ""
    survey: Path | None = None
    bluetooth: int = 0


def seed_seen(connection):
    # Recover rows from existing surveys, including one saved just before an interrupted DB commit.
    for path in surveys():
        try:
            with path.open(encoding="utf-8", newline="") as source:
                reader = csv.reader(source)
                next(reader, None)
                if next(reader, None) != list(HEADER):
                    continue
                connection.executemany(
                    "INSERT OR IGNORE INTO seen VALUES (?, ?)",
                    ((row[-1], row[0].upper()) for row in reader
                     if len(row) == len(HEADER) and row[-1] in ("WIFI", "BLE")
                     and wifi.MAC_PATTERN.fullmatch(row[0])),
                )
        except (OSError, UnicodeError, csv.Error):
            continue


def append_new(connection, survey, rows, now):
    new_rows = []
    for row in rows:
        inserted = connection.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (row[-1], row[0]))
        if inserted.rowcount:
            new_rows.append(row)
    if new_rows:
        survey = append_survey(survey, new_rows, now)
    added_wifi = sum(row[-1] == "WIFI" for row in new_rows)
    return survey, added_wifi, len(new_rows) - added_wifi


def bluetooth_rows(devices, fix, now):
    return [(address, name, "[BLE]", now.strftime("%Y-%m-%d %H:%M:%S"), 0, 0, signal,
             f"{fix.latitude:.7f}", f"{fix.longitude:.7f}", f"{fix.altitude:.1f}",
             f"{fix.accuracy:.1f}", "", "", "BLE")
            for address, name, signal in devices]


def wardrive(interface, position, cancel, report, *, interval=12, pause=None):
    if not (Path("/sys/class/net") / interface / "wireless").exists() and not (
        Path("/sys/class/net") / interface / "phy80211"
    ).exists():
        raise wifi.WifiError(f"WiFi adapter {interface} is unavailable")
    scans = networks_logged = bluetooth_logged = 0
    survey = None
    configured = survey_privacy.zones()
    allowed = wifi.allowlist()

    with lifetime.database(wifi.DATA) as connection:
        seed_seen(connection)
        lifetime.increment(connection, "wardriving", sessions=1)

    def status(phase, error=""):
        return WardriveStatus(phase, scans, networks_logged, networks_logged + bluetooth_logged,
                              error, survey, bluetooth_logged)

    def interrupted():
        return cancel.is_set() or (pause is not None and pause.is_set())

    def public_fix():
        fix = position()
        return fix if fix is not None and not survey_privacy.private(fix, configured) else None

    while wifi.wait_while_paused(cancel, pause, lambda: report(status("Paused"))):
        if configured and public_fix() is None:
            report(status("Private" if position() else "Locating"))
            wifi.wait_until_pause_or_cancel(cancel, pause, min(2, interval))
            continue
        report(status("Scanning"))
        rows = []
        error = ""
        scanned = 0
        try:
            networks = wifi.scan(interface=interface, allowed=allowed)
            scanned = 1
            if not interrupted() and public_fix() is not None:
                signals = rssi_by_bssid(interface)
                fix = public_fix()
                if fix is not None:
                    rows.extend(survey_rows(networks, signals, fix, datetime.now(timezone.utc)))
        except wifi.WifiError as failure:
            error = str(failure)

        if not interrupted() and public_fix() is not None:
            report(status("Listening", error))
            devices, bluetooth_error = ble.scan(lambda: interrupted() or public_fix() is None)
            fix = public_fix()
            if fix is not None:
                rows.extend(bluetooth_rows(devices, fix, datetime.now(timezone.utc)))
            error = error or bluetooth_error
        if interrupted() or (configured and public_fix() is None):
            continue

        with lifetime.database(wifi.DATA) as connection:
            survey, added_wifi, added_bluetooth = append_new(
                connection, survey, rows, datetime.now(timezone.utc))
            lifetime.increment(connection, "wardriving", scans=scanned, networks=added_wifi,
                               bluetooth=added_bluetooth)
        scans += scanned
        networks_logged += added_wifi
        bluetooth_logged += added_bluetooth
        report(status("Listening" if position() else "Locating", error))
        wifi.wait_until_pause_or_cancel(cancel, pause, interval)
    return status("Stopped")
