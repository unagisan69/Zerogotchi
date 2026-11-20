"""Install a newer Zerogotchi package from the official GitHub release."""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import select
import sys
import time
import subprocess
import tempfile

from wifi import WifiError


REPO = "unagisan69/Zerogotchi"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PACKAGE = "zerogotchi_all.deb"
CHECKSUM = "SHA256SUMS.txt"


@dataclass(frozen=True)
class Release:
    current: str
    latest: str
    available: bool
    size: int = 0


def installed_version():
    try:
        return subprocess.check_output(
            ["/usr/bin/dpkg-query", "-W", "-f=${Version}", "zerogotchi"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("Could not determine the installed version") from error


def download(url, destination=None, *, timeout=45, report=None, total=0):
    args = ["curl", "--disable", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "6",
            "--max-time", str(timeout), "--max-filesize", "20000000",
            "--user-agent", "Zerogotchi-updater", "--header", "Cache-Control: no-cache"]
    if destination:
        args += ["--output", str(destination)]
    try:
        if destination is None:
            return subprocess.check_output([*args, url], stderr=subprocess.DEVNULL, timeout=timeout + 3)
        started = time.monotonic()
        with subprocess.Popen([*args, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            try:
                while process.poll() is None:
                    if time.monotonic() - started > timeout + 3:
                        raise WifiError("The download timed out")
                    size = destination.stat().st_size if destination.exists() else 0
                    if report:
                        amount = f"{size / 1048576:.2f} MiB"
                        if total > 0:
                            amount += f" / {total / 1048576:.2f} MiB ({min(100, size * 100 // total)}%)"
                        report(f"Downloading\n{amount}\nElapsed {int(time.monotonic() - started)}s")
                    time.sleep(0.15)
                if process.returncode:
                    raise WifiError("Could not download the GitHub release")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        return b""
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("Could not download the GitHub release") from error


def newer(latest, current):
    result = subprocess.run(["/usr/bin/dpkg", "--compare-versions", latest, "gt", current],
                            timeout=5, capture_output=True)
    if result.returncode not in (0, 1):
        raise WifiError("Could not compare package versions")
    return result.returncode == 0


def check(*, timeout=12):
    current = installed_version()
    try:
        release = json.loads(download(API, timeout=timeout))
        tag = release["tag_name"]
        assets = {asset["name"]: asset for asset in release["assets"]}
        if release.get("draft") or release.get("prerelease"):
            raise WifiError("No stable release available")
    except (ValueError, KeyError, TypeError) as error:
        raise WifiError("GitHub returned an unreadable release") from error
    if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise WifiError("GitHub release uses an unsupported version")
    latest = tag[1:]
    available = newer(latest, current)
    if available and not {PACKAGE, CHECKSUM} <= assets.keys():
        raise WifiError("The latest release has no verified Debian download yet")
    size = assets.get(PACKAGE, {}).get("size", 0)
    if type(size) is not int or not 0 <= size <= 20_000_000:
        raise WifiError("GitHub returned an invalid package size")
    return Release(current, latest, available, size)


def install(release, password, report=lambda message: None):
    if not release.available or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", release.latest):
        raise WifiError("No newer release selected")
    if not newer(release.latest, installed_version()):
        return
    base = f"https://github.com/{REPO}/releases/download/v{release.latest}"
    report("Reading the release checksum…")
    checksum = download(f"{base}/{CHECKSUM}")
    matches = re.findall(rb"^([a-fA-F0-9]{64})[ \t]+\*?zerogotchi_all\.deb\r?$",
                         checksum, re.MULTILINE)
    if len(matches) != 1:
        raise WifiError("The release checksum could not be read")
    with tempfile.TemporaryDirectory(prefix="zerogotchi-update-") as directory:
        folder = Path(directory)
        folder.chmod(0o755)
        package = folder / PACKAGE
        download(f"{base}/{PACKAGE}", package, report=report, total=release.size)
        report("Verifying the SHA-256 checksum…")
        with package.open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest().encode()
        if actual != matches[0].lower():
            raise WifiError("Download failed the SHA-256 check")
        report("Checking the Debian package…")
        try:
            fields = subprocess.check_output(
                ["/usr/bin/dpkg-deb", "-f", str(package), "Package", "Version", "Architecture"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise WifiError("Download is not a readable Debian package") from error
        expected = {"Package: zerogotchi", f"Version: {release.latest}", "Architecture: all"}
        if set(fields.splitlines()) != expected:
            raise WifiError("Downloaded package metadata does not match the release")
        run_installer(package, password, report)
        report("Checking the installed version…")
        if installed_version() != release.latest:
            raise WifiError("The installed version does not match the update")


def run_installer(package, password, report):
    started = time.monotonic()
    report("Installing the update…")
    try:
        with subprocess.Popen(
            ["sudo", "-Sk", "-p", "", "--", "/usr/bin/apt-get", "-y",
             "-o", "APT::Status-Fd=1", "-o", "Dpkg::Use-Pty=0", "install", str(package)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        ) as process:
            try:
                process.stdin.write(password.encode() + b"\n")
                process.stdin.close()
                del password
                buffer = b""
                percent = None
                while process.poll() is None:
                    if time.monotonic() - started > 300:
                        raise WifiError("Installation timed out. Check the package manager before retrying.")
                    if select.select([process.stdout], [], [], 0.2)[0]:
                        buffer += os.read(process.stdout.fileno(), 8192)
                        lines = buffer.split(b"\n")
                        buffer = lines.pop()[-8192:]
                        for line in lines:
                            match = re.match(rb"(?:pmstatus|dlstatus):[^:]*:([0-9.]+):", line)
                            if match:
                                percent = min(100, max(0, int(float(match[1]))))
                    detail = f"Package progress {percent}%" if percent is not None else "Preparing packages"
                    report(f"Installing\n{detail}\nElapsed {int(time.monotonic() - started)}s")
                if process.returncode:
                    raise WifiError("Install failed. Check your Cardputer password and APT connection.")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("Package installation could not finish") from error


if __name__ == "__main__" and sys.argv[1:] == ["--startup-check"]:
    try:
        release = check(timeout=4)
        if release.available:
            print(json.dumps(asdict(release)), flush=True)
    except (WifiError, OSError):
        pass
