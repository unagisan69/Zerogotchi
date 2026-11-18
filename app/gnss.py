"""Observe the stock Cardputer GNSS UART during an Auto session."""

import os
from pathlib import Path
import re
import subprocess
import termios
import tty
from dataclasses import dataclass
import math


PORT = Path("/dev/serial0")
RAIL = Path("/sys/class/leds/ext_5v_out/brightness")
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
    def __init__(self, helper, password, *, enable_power=True):
        self.fd = self.saved = self.power = None
        self.partial = b""
        self.last_seen = float("-inf")
        self.fix = None
        self.in_view = {}
        try:
            if not PORT.exists() or not RAIL.exists():
                return
            device = PORT.resolve()
            boot = Path("/proc/cmdline").read_text().split()
            if any(option.split(",")[0] in ("console=serial0", f"console={device.name}") for option in boot):
                return
            self.fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            mode = termios.tcgetattr(self.fd)
            mode[4] = mode[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, mode)
            if enable_power and RAIL.read_text().strip() == "0":
                self.power = subprocess.Popen(
                    ["sudo", "-Sk", "-p", "", "--", helper, "gnss"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self.power.stdin.write(password.encode() + b"\n")
                self.power.stdin.flush()
        except (OSError, termios.error):
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
