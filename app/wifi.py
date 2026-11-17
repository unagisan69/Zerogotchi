"""Wi-Fi discovery, capture storage, and OnlineHashCrack submission."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
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


def convert(pcap):
    output = pcap.with_suffix(".22000")
    try:
        subprocess.run(
            ["hcxpcapngtool", "-o", str(output), str(pcap)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True,
        )
        if output.exists():
            bssid = pcap.parent.name.split("-")[0].lower()
            target_hashes = [
                line for line in output.read_text(encoding="ascii").splitlines()
                if (fields := line.split("*"))[:2] in (["WPA", "01"], ["WPA", "02"])
                and len(fields) > 3 and fields[3].lower() == bssid
            ]
            if target_hashes:
                output.write_text("\n".join(dict.fromkeys(target_hashes)) + "\n", encoding="ascii")
                return output
            output.unlink()
        return None
    except (subprocess.SubprocessError, OSError, UnicodeError) as error:
        output.unlink(missing_ok=True)
        raise WifiError("Conversion failed. Capture saved.") from error


def capture(network, interface, client, password, cancel):
    if not interface:
        raise WifiError("Set capture adapter in Settings")
    CAPTURES.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=network.bssid.replace(":", "") + "-", dir=CAPTURES))
    pcap = folder / "capture.pcap"
    prompt = "ZEROGOTCHI_SUDO:"
    args = ["sudo", "-Sk", "-p", prompt, "--", HELPER, interface, str(network.frequency), network.bssid]
    if client:
        args.append(client)
    try:
        with pcap.open("xb") as output, tempfile.TemporaryFile() as errors:
            os.chmod(pcap, 0o600)
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=errors)
            with process:
                try:
                    process.stdin.write(password.encode() + b"\n")
                    process.stdin.flush()
                except BrokenPipeError:
                    pass
                while process.poll() is None and not cancel.wait(0.2):
                    if os.pread(errors.fileno(), 4096, 0).count(prompt.encode()) > 1:
                        break
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                status = process.wait()
            errors.seek(0)
            detail = errors.read(2048).decode(errors="replace")
        if pcap.stat().st_size <= 24:
            pcap.unlink()
            folder.rmdir()
            if cancel.is_set():
                raise WifiError("Capture canceled")
            if "ZEROGOTCHI:" in detail:
                raise WifiError(detail.split("ZEROGOTCHI:", 1)[1].splitlines()[0].strip())
            if prompt in detail and status:
                raise WifiError("Admin authentication failed")
            raise WifiError("No packets. Check capture adapter")
        if status:
            raise WifiError("Capture interrupted. PCAP saved.")
        return pcap, convert(pcap), "Deauth failed" in detail
    except (OSError, subprocess.SubprocessError) as error:
        if pcap.exists() and pcap.stat().st_size <= 24:
            pcap.unlink()
            folder.rmdir()
        raise WifiError("Capture failed. Check adapter and permissions") from error


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
