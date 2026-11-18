"""Observe the stock Cardputer GNSS UART during an Auto session."""

import os
from pathlib import Path
import re
import subprocess
import termios
import tty


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


class GnssMonitor:
    def __init__(self, helper, password):
        self.fd = self.saved = self.power = None
        self.partial = b""
        self.last_seen = float("-inf")
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
            if RAIL.read_text().strip() == "0":
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
            if any(valid_sentence(line.rstrip(b"\r")) for line in lines):
                self.last_seen = now
        return now - self.last_seen < 5

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
