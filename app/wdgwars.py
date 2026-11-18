"""WDG Wars API-key check and WiGLE CSV upload."""

import csv
from itertools import islice
import json
from pathlib import Path
import re
import subprocess
import tempfile

import wifi
import wigle


BASE_URL = "https://wdgwars.pl"
KEY = re.compile(r"[0-9a-fA-F]{64}")


def credentials():
    key = wifi.read_setting("wdgwars-key")
    return key if KEY.fullmatch(key) else None


def request(path, survey=None):
    key = credentials()
    if not key:
        raise wifi.WifiError("Save your WDG Wars API key first")
    args = ["curl", "--disable", "--silent", "--show-error", "--max-time", "115" if survey else "15",
            "--proto", "=https", "--header", "Accept: application/json",
            "--config", "-", "--write-out", "\n%{http_code}"]
    if survey:
        filename = str(survey).replace("\\", "\\\\").replace('"', '\\"')
        args += ["--form", f'file=@"{filename}";type=text/csv']
    try:
        response = subprocess.run([*args, BASE_URL + path], text=True, capture_output=True,
                                  input=f'header = "X-API-Key: {key}"\n', timeout=120 if survey else 17)
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
    if status == "409" and result.get("error") == "duplicate_upload" and survey:
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


def upload(path):
    imported = duplicates = rejected = 0
    with wigle.export_survey(path) as export, tempfile.TemporaryDirectory(prefix="zg-wdgwars-") as folder:
        with export.open(encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            headers = (next(reader), next(reader))
            for part, rows in enumerate(iter(lambda: list(islice(reader, 10_000)), []), 1):
                chunk = Path(folder) / f"{export.stem}-{part}.csv"
                with chunk.open("w", encoding="utf-8", newline="") as output:
                    writer = csv.writer(output)
                    writer.writerows(headers)
                    writer.writerows(rows)
                result = request("/endpoint/upload-csv", chunk)
                if not result:
                    duplicates += len(rows)
                else:
                    counts = [result.get(name, 0) for name in ("imported", "duplicates", "bad_rows", "no_gps")]
                    if any(type(count) is not int or count < 0 for count in counts):
                        raise wifi.WifiError("WDG Wars accepted the upload but returned invalid counts")
                    imported += counts[0]
                    duplicates += counts[1]
                    rejected += counts[2] + counts[3]
    return f"Imported {imported}. Duplicates {duplicates}. Rejected {rejected}."
