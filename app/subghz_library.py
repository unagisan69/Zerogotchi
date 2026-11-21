"""Optional user-owned signal Library. Import is independent of radio access."""
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zlib

import subghz
import wifi

REPO = 'Zero-Sploit/FlipperZero-Subghz-DB'
SOURCE = f'https://github.com/{REPO}/tree/main/subghz'
ARCHIVE = f'https://api.github.com/repos/{REPO}/tarball/main'
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
EXCLUDED = {'jamming', 'debruijn', 'smoke_alarm', 'training_collars',
            'el50448_-tpms_relearn_tool', 'vehicles', 'handicap'}


@dataclass(frozen=True)
class Progress:
    phase: str = 'Connecting'
    current: int = 0
    total: int = 0
    imported: int = 0
    skipped: int = 0
    detail: str = ''


def root():
    folder = wifi.DATA / 'subghz' / 'library'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder.resolve()


def entries(folder=None):
    base = root()
    folder = base if folder is None else Path(folder)
    if folder.is_symlink() or not folder.resolve().is_relative_to(base):
        raise wifi.WifiError('Library folders must stay inside the Library.')
    return sorted((p for p in folder.iterdir() if not p.is_symlink() and not p.name.startswith('.')
                   and (p.is_dir() or (p.is_file() and p.suffix.lower() in ('.sub', '.json')))),
                  key=lambda p: (not p.is_dir(), p.name.casefold()))


def allowed(parts):
    label = '/'.join(parts).casefold()
    return (not any(part.casefold() in EXCLUDED for part in parts)
            and not any(word in label for word in ('jamming', 'jammer', 'debruijn', 'bruteforce', 'brute_force', 'brute-force')))


def cancelled(event):
    if event.is_set():
        raise wifi.WifiError('Library import cancelled. Completed files are kept; import again to continue.')


def unpack(archive, destination, cancel, report):
    """Never extract tar paths/links. Only validate and copy allowed OOK files."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    counts = Counter()
    expanded = members = 0
    next_report = 0
    total = Path(archive).stat().st_size
    with Path(archive).open('rb') as raw, tarfile.open(fileobj=raw, mode='r|gz') as bundle:
        for member in bundle:
            cancelled(cancel)
            members += 1
            expanded += max(0, member.size)
            if members > 50_000 or expanded > MAX_EXPANDED:
                raise wifi.WifiError('Source archive exceeds import limits.')
            parts = PurePosixPath(member.name).parts
            if len(parts) < 4 or parts[1] != 'subghz' or not member.name.lower().endswith('.sub'):
                continue
            counts['examined'] += 1
            relative = PurePosixPath(*parts[2:])
            if (not member.isfile() or relative.is_absolute() or '..' in relative.parts
                    or '\\' in str(relative) or not allowed(relative.parts)):
                counts['excluded'] += 1
            elif not 0 < member.size <= 2_000_000:
                counts['unsupported'] += 1
            else:
                target = destination.joinpath(*relative.parts)
                if target.is_symlink() or not target.parent.resolve().is_relative_to(destination):
                    counts['excluded'] += 1
                elif target.exists():
                    counts['existing'] += 1
                else:
                    with bundle.extractfile(member) as source:
                        content = source.read(2_000_001)
                    try:
                        subghz.parse_flipper_raw(content.decode('utf-8-sig'))
                    except (wifi.WifiError, UnicodeError, ValueError):
                        counts['unsupported'] += 1
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        # Same-filesystem atomic publication; cancellation cannot expose a partial .sub.
                        fd, name = tempfile.mkstemp(prefix='.import-', dir=target.parent)
                        try:
                            with os.fdopen(fd, 'wb') as output:
                                output.write(content)
                            cancelled(cancel)
                            os.link(name, target)  # No overwriting SSH-added or concurrently created files.
                            counts['imported'] += 1
                        except FileExistsError:
                            counts['existing'] += 1
                        finally:
                            Path(name).unlink(missing_ok=True)
            now = time.monotonic()
            if now >= next_report:
                report(Progress('Importing', min(raw.tell(), total), total, counts['imported'],
                                counts['excluded'] + counts['unsupported'] + counts['existing'],
                                f"{counts['examined']} files checked"))
                next_report = now + .15
    if not counts['examined']:
        raise wifi.WifiError('No Sub-Ghz files found in the source archive.')
    return dict(counts)


def download_import(cancel, report):
    folder = root()
    # Archive and final recordings share the user's filesystem; reserve space before downloading.
    if shutil.disk_usage(folder).free < MAX_ARCHIVE + 512*1024*1024:
        raise wifi.WifiError('Library import needs at least 768 MB of free storage.')
    cancelled(cancel)
    report(Progress())
    try:
        with tempfile.TemporaryDirectory(prefix='.download-', dir=folder) as temporary:
            archive = Path(temporary) / 'source.tar.gz'
            request = urllib.request.Request(ARCHIVE, headers={'User-Agent': 'Zerogotchi-Library', 'Accept': 'application/vnd.github+json'})
            with urllib.request.urlopen(request, timeout=15) as response, archive.open('wb') as output:
                length = int(response.headers.get('Content-Length') or 0)
                if length < 0 or length > MAX_ARCHIVE:
                    raise wifi.WifiError('Source archive is larger than the 256 MB download limit.')
                received = 0
                next_report = 0
                while True:
                    cancelled(cancel)
                    chunk = response.read(128*1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_ARCHIVE:
                        raise wifi.WifiError('Source archive exceeded the download limit.')
                    output.write(chunk)
                    now = time.monotonic()
                    if now >= next_report:
                        report(Progress('Downloading', received, length, detail=f'{received/1048576:.1f} MB received'))
                        next_report = now + .15
                if length and received != length:
                    raise wifi.WifiError('Download was interrupted. Please retry Library import.')
            cancelled(cancel)
            report(Progress('Importing', detail='Checking supported files…'))
            destination = folder / 'Zero-Sploit'
            if destination.is_symlink():
                raise wifi.WifiError('Import destination must not be a symlink.')
            counts = unpack(archive, destination, cancel, report)
            metadata = folder / '.last-import.json'
            if metadata.is_symlink():
                raise wifi.WifiError('Import metadata must not be a symlink.')
            metadata.write_text(json.dumps({'source': SOURCE, 'completed': time.time(), 'counts': counts}, indent=2))
            metadata.chmod(0o600)
            return counts
    except (urllib.error.URLError, TimeoutError) as error:
        raise wifi.WifiError('Library download failed. Check internet access and try again.') from error
    except (tarfile.TarError, EOFError, ValueError, zlib.error) as error:
        raise wifi.WifiError('GitHub returned an invalid or incomplete source archive.') from error
