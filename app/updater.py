"""Install a newer Zerogotchi package from the official GitHub release."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
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


def installed_version():
    try:
        return subprocess.check_output(
            ["/usr/bin/dpkg-query", "-W", "-f=${Version}", "zerogotchi"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("Could not determine the installed version") from error


def download(url, destination=None):
    args = ["curl", "--disable", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "6",
            "--max-time", "45", "--max-filesize", "20000000"]
    if destination:
        args += ["--output", str(destination)]
    try:
        return subprocess.check_output([*args, url], stderr=subprocess.DEVNULL, timeout=48)
    except (OSError, subprocess.SubprocessError) as error:
        raise WifiError("Could not download the GitHub release") from error


def newer(latest, current):
    result = subprocess.run(["/usr/bin/dpkg", "--compare-versions", latest, "gt", current],
                            timeout=5, capture_output=True)
    if result.returncode not in (0, 1):
        raise WifiError("Could not compare package versions")
    return result.returncode == 0


def check():
    current = installed_version()
    try:
        release = json.loads(download(API))
        tag = release["tag_name"]
        assets = {asset["name"] for asset in release["assets"]}
    except (ValueError, KeyError, TypeError) as error:
        raise WifiError("GitHub returned an unreadable release") from error
    if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise WifiError("GitHub release uses an unsupported version")
    latest = tag[1:]
    available = newer(latest, current)
    if available and not {PACKAGE, CHECKSUM} <= assets:
        raise WifiError("The latest release has no verified Debian download yet")
    return Release(current, latest, available)


def install(release, password):
    if not release.available or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", release.latest):
        raise WifiError("No newer release selected")
    if not newer(release.latest, installed_version()):
        return
    base = f"https://github.com/{REPO}/releases/download/v{release.latest}"
    checksum = download(f"{base}/{CHECKSUM}")
    matches = re.findall(rb"^([a-fA-F0-9]{64})[ \t]+\*?zerogotchi_all\.deb\r?$",
                         checksum, re.MULTILINE)
    if len(matches) != 1:
        raise WifiError("The release checksum could not be read")
    with tempfile.TemporaryDirectory(prefix="zerogotchi-update-") as directory:
        folder = Path(directory)
        folder.chmod(0o755)
        package = folder / PACKAGE
        download(f"{base}/{PACKAGE}", package)
        with package.open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest().encode()
        if actual != matches[0].lower():
            raise WifiError("Download failed the SHA-256 check")
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
        try:
            result = subprocess.run(
                ["sudo", "-Sk", "-p", "", "--", "/usr/bin/apt-get", "-y", "install", str(package)],
                input=password + "\n", text=True, capture_output=True, timeout=240,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise WifiError("Package installation could not finish") from error
        if result.returncode or installed_version() != release.latest:
            raise WifiError("Install failed. Check your Cardputer password and APT connection.")
