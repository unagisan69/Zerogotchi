"""Receive NMEA from the Cap or Grove GPS on the stock Cardputer UART."""

import os
import fcntl
from pathlib import Path
import re
import subprocess
import termios
import tty
from dataclasses import dataclass
import math
import time

import wifi


PORT = Path("/dev/serial0")
CMDLINE = Path("/proc/cmdline")
RAIL = Path("/sys/class/leds/ext_5v_out/brightness")
GROVE_POWER = Path("/sys/class/leds/grove_5v_out/brightness")
GROVE_ROUTE = Path("/sys/class/leds/grove_fun/brightness")
SOURCES = {"cap": "GNSS HAT / Cap", "grove": "Grove GPS v1.1 (U032-V11)"}


def source_key():
    value = wifi.read_setting("gps-source") or "cap"
    return value if value in SOURCES else "cap"


SENTENCE = re.compile(rb"^\$[A-Z]{2}(?:GGA|GLL|GNS|GSA|GSV|RMC|VTG|ZDA),[^*]*\*[0-9A-Fa-f]{2}$")


def valid_sentence(line):
    if not SENTENCE.fullmatch(line):
        return False
    checksum = 0
    for byte in line[1:-3]:
        checksum ^= byte
    return checksum == int(line[-2:], 16)


@dataclass(frozen=True)
class Fix:
    latitude: float
    longitude: float
    altitude: float
    accuracy: float
    received_at: float


def parse_fix(line, now):
    fields = line[:-3].split(b",")
    if not fields[0].endswith(b"GGA"):
        return None
    try:
        if not 1 <= int(fields[6]) <= 5:
            return None
        coords = []
        for value, direction, positive, negative, maximum in (
            (fields[2], fields[3], b"N", b"S", 90),
            (fields[4], fields[5], b"E", b"W", 180),
        ):
            number = float(value)
            if not math.isfinite(number) or number < 0 or direction not in (positive, negative):
                return None
            degrees, minutes = divmod(number, 100)
            decimal = degrees + minutes / 60
            if minutes >= 60 or decimal > maximum:
                return None
            coords.append(decimal if direction == positive else -decimal)
        altitude, hdop = float(fields[9]), float(fields[8])
        if not math.isfinite(altitude) or not math.isfinite(hdop) or hdop <= 0 or fields[10] != b"M":
            return None
        return Fix(*coords, altitude, hdop * 5, now)
    except (IndexError, ValueError):
        return None


def parse_satellites(line):
    fields = line[:-3].split(b",")
    try:
        count = int(fields[3])
        part = int(fields[2])
    except (IndexError, ValueError):
        return None
    if part != 1 or not 0 <= count <= 255:
        return None
    talker = fields[0][1:3]
    return (b"GB" if talker == b"BD" else talker), count


class GnssMonitor:
    def __init__(self, helper, password, *, enable_power=True, source=None):
        self.fd = self.saved = self.power = None
        self.source = source if source in SOURCES else source_key()
        self.controls = []
        self.error = ""
        self.partial = b""
        self.last_seen = float("-inf")
        self.fix = None
        self.in_view = {}
        try:
            if not PORT.exists():
                self.error = "GPS UART unavailable (/dev/serial0)."
                return
            device = PORT.resolve()
            boot = CMDLINE.read_text().split()
            if any(option.split(",")[0] in ("console=serial0", f"console={device.name}") for option in boot):
                self.error = "GPS UART is used by the serial console."
                return
            self.fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.error = "GPS UART is already in use by another Zerogotchi session."
                self.close()
                return
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            mode = termios.tcgetattr(self.fd)
            # Explicit 115200 8N1, local receiver, no hardware/software flow control.
            mode[2] &= ~(termios.CSIZE | termios.PARENB | termios.PARODD | termios.CSTOPB
                         | getattr(termios, "CRTSCTS", 0))
            mode[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
            mode[0] &= ~(termios.IXON | termios.IXOFF | getattr(termios, "IXANY", 0))
            mode[4] = mode[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, mode)
            if self.source == "grove":
                settings = ((GROVE_ROUTE, "0"), (GROVE_POWER, "1"))
                original = [(path, path.read_text().strip(), wanted) for path, wanted in settings]
                if any(not value.isdecimal() or not 0 <= int(value) <= 255 for _, value, _ in original):
                    raise OSError("Invalid Grove control state")
                if not enable_power and any(bool(int(value)) != bool(int(wanted)) for _, value, wanted in original):
                    self.error = "Grove GPS inactive: UART mode and Grove 5V power are required."
                    self.close()
                    return
                for path, value, wanted in original:
                    if bool(int(value)) != bool(int(wanted)):
                        path.write_text(wanted)
                        self.controls.append((path, value))
            elif enable_power and RAIL.exists() and RAIL.read_text().strip() == "0":
                self.power = subprocess.Popen(
                    ["sudo", "-Sk", "-p", "", "--", helper, "gnss"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self.power.stdin.write(password.encode() + b"\n")
                self.power.stdin.flush()
        except (OSError, termios.error) as error:
            self.error = f"GPS access failed: {error}"
            self.close()

    def poll(self, now):
        if self.fd is None:
            return False
        try:
            received = os.read(self.fd, 4096)
        except BlockingIOError:
            received = b""
        except OSError:
            self.close()
            return False
        if received:
            lines = (self.partial + received).split(b"\n")
            self.partial = lines.pop()[-256:]
            for raw in lines:
                line = raw.rstrip(b"\r")
                if valid_sentence(line):
                    self.last_seen = now
                    if line[3:6] == b"GGA":
                        self.fix = parse_fix(line, now)
                    elif line[3:6] == b"GSV":
                        observation = parse_satellites(line)
                        if observation:
                            talker, count = observation
                            self.in_view[talker] = count, now
        return now - self.last_seen < 5

    def position(self, now):
        fix = self.fix
        return fix if fix and now - fix.received_at < 5 else None

    def satellites(self, now):
        fresh = {talker: count for talker, (count, timestamp) in self.in_view.items()
                 if now - timestamp < 10}
        if not fresh:
            return None
        return fresh[b"GN"] if b"GN" in fresh else sum(fresh.values())

    def close(self):
        # Restore only controls we changed, in reverse order (power, then mux).
        for path, value in reversed(self.controls):
            try:
                path.write_text(value)
            except OSError:
                self.error = "Could not restore a Grove control; check GPS port settings."
        self.controls.clear()
        if self.fd is not None:
            try:
                if self.saved is not None:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
            except (OSError, termios.error):
                pass
            finally:
                os.close(self.fd)
                self.fd = None
        if self.power is not None:
            try:
                self.power.stdin.close()
            except BrokenPipeError:
                pass
            try:
                self.power.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # EOF still releases the rail when the helper finishes starting.
                pass
            self.power = None



def check(helper, password, cancel, report):
    """Bounded foreground check, powering only the selected GPS for its lifetime."""
    monitor = GnssMonitor(helper, password)
    try:
        deadline = time.monotonic() + 6
        while monitor.fd is not None and not cancel.is_set() and time.monotonic() < deadline:
            now = time.monotonic()
            receiving = monitor.poll(now)
            report("Receiving NMEA…" if receiving else "Waiting for NMEA…")
            cancel.wait(.2)
        now = time.monotonic()
        active = now - monitor.last_seen < 5
        fix = monitor.position(now)
        satellites = monitor.satellites(now)
        lines = [SOURCES[monitor.source], "115200 baud · 8N1",
                 "NMEA: receiving" if active else "NMEA: no recent data",
                 f"Satellites: {satellites if satellites is not None else 'unknown'}",
                 "Fix: current" if fix else "Fix: waiting"]
        if monitor.error:
            lines = [SOURCES[monitor.source], monitor.error]
        result = "\n".join(lines)
    finally:
        monitor.close()
    return "\n".join([SOURCES[monitor.source], monitor.error]) if monitor.error else result
