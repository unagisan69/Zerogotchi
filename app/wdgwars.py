"""WDG Wars account checks and signed survey upload."""

import base64
from collections import Counter
import csv
import hashlib
import hmac
from itertools import islice
import json
import re
import secrets
import subprocess

import wifi
import wigle


BASE_URL = "https://wdgwars.pl"
KEY = re.compile(r"[0-9a-fA-F]{64}")


def credentials():
    key = wifi.read_setting("wdgwars-key")
    return key if KEY.fullmatch(key) else None


def request(path, *, networks=None, meshcore_nodes=None):
    uploading = networks is not None or meshcore_nodes is not None
    key = credentials()
    if not key:
        raise wifi.WifiError("Save your WDG Wars API key first")
    args = ["curl", "--disable", "--silent", "--show-error", "--max-time", "115" if uploading else "15",
            "--proto", "=https", "--header", "Accept: application/json",
            "--config", "-", "--write-out", "\n%{http_code}"]
    config = f'header = "X-API-Key: {key}"\n'
    if uploading:
        # The official client signs the nonce followed by the base64 JSON.
        payload = {"networks": networks or []}
        if meshcore_nodes is not None:
            payload["meshcore_nodes"] = meshcore_nodes
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        data = base64.b64encode(raw).decode("ascii")
        nonce = secrets.token_hex(8)
        signature = hmac.new(key.encode("ascii"), (nonce + data).encode("ascii"), hashlib.sha256).hexdigest()
        payload = json.dumps({"data": data, "nonce": nonce, "sig": signature}, separators=(",", ":"))
        config += 'header = "Content-Type: application/json"\n'
        config += 'data-binary = ' + json.dumps(payload) + "\n"
    try:
        response = subprocess.run([*args, BASE_URL + path], text=True, capture_output=True,
                                  input=config, timeout=120 if uploading else 17)
    except (OSError, subprocess.SubprocessError) as error:
        raise wifi.WifiError("WDG Wars connection failed") from error
    if response.returncode:
        raise wifi.WifiError("WDG Wars connection failed")
    payload, _, status = response.stdout.rpartition("\n")
    try:
        result = json.loads(payload)
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    if status == "409" and result.get("error") == "duplicate_upload" and uploading:
        return {}
    if status == "429":
        wait = result.get("retry_after")
        detail = f"Retry in {wait}s." if isinstance(wait, int) and wait > 0 else "Try again later."
        raise wifi.WifiError(f"WDG Wars upload queue is busy. {detail}")
    if not status.isdecimal() or not 200 <= int(status) < 300:
        raise wifi.WifiError(f"WDG Wars returned HTTP {status if status.isdecimal() else 'unknown'}")
    if result.get("ok") is not True:
        raise wifi.WifiError("WDG Wars did not accept the request")
    return result


def test_profile():
    request("/api/me")
    return "WDG Wars API key verified."


def network_record(row):
    auth = row["AuthMode"].upper()
    if row["Type"] == "BLE":
        auth = "BLE"
    elif "SAE" in auth or "WPA3" in auth:
        auth = "WPA3"
    elif "WPA2" in auth:
        auth = "WPA2-EAP" if "EAP" in auth else "WPA2"
    elif "WPA" in auth:
        auth = "WPA"
    elif "WEP" in auth:
        auth = "WEP"
    else:
        auth = "OPEN"
    return {
        "bssid": row["MAC"], "ssid": row["SSID"], "auth": auth, "first_seen": row["FirstSeen"],
        "channel": int(row["Channel"]), "rssi": int(row["RSSI"]),
        "lat": float(row["CurrentLatitude"]), "lon": float(row["CurrentLongitude"]),
        "alt": float(row["AltitudeMeters"]), "type": row["Type"],
    }


def upload(path):
    imported = duplicates = rejected = submitted = 0
    with wigle.export_survey(path) as export, export.open(encoding="utf-8", newline="") as source:
        next(source)
        rows = csv.DictReader(source)
        for batch in iter(lambda: list(islice(rows, 1_000)), []):
            try:
                records = [network_record(row) for row in batch]
            except (TypeError, ValueError) as error:
                raise wifi.WifiError("A survey row has invalid radio or GPS data") from error
            result = request("/api/upload/", networks=records)
            submitted += len(records)
            if not result:
                duplicates += len(records)
                continue
            counts = [result.get(name, 0) for name in ("imported", "duplicates", "bad_rows", "no_gps")]
            if any(type(count) is not int or count < 0 for count in counts):
                raise wifi.WifiError("WDG Wars returned invalid upload counts")
            imported += counts[0]
            duplicates += counts[1]
            rejected += counts[2] + counts[3]
    message = f"Imported {imported}. Duplicates {duplicates}. Rejected {rejected}."
    unreported = submitted - imported - duplicates - rejected
    if unreported > 0:
        message += f" No result for {unreported}."
    return message


def upload_mesh(records):
    imported = already_seen = merged = rejected = 0
    reasons = Counter()
    for start in range(0, len(records), 1_000):
        batch = records[start:start + 1_000]
        result = request("/api/upload/", meshcore_nodes=batch)
        if not result:
            already_seen += len(batch)
            continue
        counts = [result.get(f"meshcore_{key}", 0)
                  for key in ("imported", "already_seen", "merged", "rejected")]
        if any(type(count) is not int or count < 0 for count in counts):
            raise wifi.WifiError("WDG Wars returned invalid mesh upload counts")
        imported += counts[0]
        already_seen += counts[1]
        merged += counts[2]
        rejected += counts[3]
        detail = result.get("meshcore_reject_reasons")
        if isinstance(detail, dict):
            reasons.update({key.replace("_", " "): count for key, count in detail.items()
                            if re.fullmatch(r"[a-z_]{1,40}", key) and type(count) is int and count > 0})
    message = f"MeshCore imported {imported}. Already seen {already_seen}. Merged {merged}. Rejected {rejected}."
    if rejected and reasons:
        message += "\n" + ", ".join(f"{reason} ({count})" for reason, count in reasons.most_common(3))
    unreported = len(records) - imported - already_seen - merged - rejected
    if unreported > 0:
        message += f" No result for {unreported}."
    return message
