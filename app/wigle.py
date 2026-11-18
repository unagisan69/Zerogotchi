"""Local WiGLE surveys and account API, independent of WPA captures."""

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from dataclasses import dataclass
import uuid

import wifi


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


def upload(path):
    if (path.parent != SURVEYS or not re.fullmatch(r"survey-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}\.csv", path.name)
            or path.is_symlink() or not path.is_file()):
        raise wifi.WifiError("Invalid survey file")
    request("/file/upload", path)
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


def record_survey(position, cancel):
    if cancel.is_set() or position() is None:
        return None
    networks = wifi.scan(interface="wlan0")
    signals = rssi_by_bssid()
    fix = position()
    if fix is None or cancel.is_set():
        return None
    now = datetime.now(timezone.utc)
    rows = survey_rows(networks, signals, fix, now)
    if rows and not cancel.is_set():
        return append_survey(None, rows, now)
    return None


@dataclass(frozen=True)
class WardriveStatus:
    phase: str
    scans: int = 0
    networks: int = 0
    observations: int = 0
    error: str = ""


def wardrive(interface, position, cancel, report, *, interval=12):
    if not (Path("/sys/class/net") / interface / "wireless").exists() and not (
        Path("/sys/class/net") / interface / "phy80211"
    ).exists():
        raise wifi.WifiError(f"WiFi adapter {interface} is unavailable")
    seen = set()
    scans = observations = 0
    survey = None
    while not cancel.is_set():
        report(WardriveStatus("Scanning", scans, len(seen), observations))
        error = ""
        try:
            networks = wifi.scan(interface=interface)
            scans += 1
            seen.update(network.bssid for network in networks)
            if position() is not None and not cancel.is_set():
                signals = rssi_by_bssid(interface)
                fix = position()
                if fix is not None and not cancel.is_set():
                    now = datetime.now(timezone.utc)
                    rows = survey_rows(networks, signals, fix, now)
                    if rows:
                        survey = append_survey(survey, rows, now)
                        observations += len(rows)
        except wifi.WifiError as failure:
            error = str(failure)
        phase = "Retrying" if error else "Listening" if position() else "Locating"
        report(WardriveStatus(phase, scans, len(seen), observations, error))
        cancel.wait(interval)
    return WardriveStatus("Stopped", scans, len(seen), observations)
