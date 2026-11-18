"""Collect fresh Bluetooth LE signal observations through BlueZ."""

import os
import re
import subprocess
import tempfile
import time


DEVICE = re.compile(r"Device ((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}) (.*)")
COLOR = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def observations(output):
    names = {}
    signals = {}
    for line in COLOR.sub("", output).splitlines():
        match = DEVICE.search(line)
        if not match:
            continue
        address = match[1].upper()
        detail = match[2].strip()
        if detail.startswith("RSSI:"):
            value = re.fullmatch(r"RSSI:\s+(?:0x[0-9a-fA-F]+\s+\()?(-?\d+)\)?", detail)
            if value and -127 <= int(value[1]) <= 20:
                signals[address] = int(value[1])
        elif detail.startswith("Name:"):
            names[address] = detail[5:].strip()
        elif "[NEW]" in line and detail.upper() != address.replace(":", "-"):
            names[address] = detail
    return [(address, names.get(address, ""), signal) for address, signal in signals.items()]


def scan(interrupted, seconds=5):
    if interrupted():
        return [], ""
    with tempfile.TemporaryFile() as output:
        try:
            with subprocess.Popen(
                ["bluetoothctl", "--timeout", str(seconds), "scan", "le"],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, "LC_ALL": "C"},
            ) as process:
                deadline = time.monotonic() + seconds + 2
                while process.poll() is None:
                    if interrupted() or time.monotonic() >= deadline:
                        process.kill()
                        return [], ""
                    time.sleep(0.1)
            output.seek(0)
            data = output.read().decode(errors="replace")
        except OSError:
            return [], "Bluetooth unavailable"
    if "Discovery started" not in data:
        return [], "Bluetooth unavailable"
    return observations(data), ""
