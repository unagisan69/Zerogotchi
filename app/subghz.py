"""CC1101 signal-strength monitoring on documented Cardputer cap pinouts."""

from collections import deque
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, replace
import fcntl
import math
from pathlib import Path
import re
import subprocess
import time

import wifi


@dataclass(frozen=True)
class Hat:
    name: str
    cs: int | None
    inputs: tuple = ()
    outputs: tuple = ()
    band_switch: int | None = None
    power: int | None = None


HATS = {
    "rf2-v1": Hat("Kasiin RF 2in1 v1", 22, inputs=(23,)),
    "rf2-v2": Hat("Kasiin RF 2in1 v2", 15, outputs=((22, 1), (23, 0))),
    "pingequa": Hat("Pingequa CC1101 + NRF24", 15, outputs=((22, 1), (23, 0))),
    "m5-cap": Hat("M5Stack Cap CC1101", None, inputs=(15,), outputs=((22, 1),),
                  band_switch=14, power=26),
}
PRESETS = (315.0, 433.92, 868.0, 915.0)
BANDS = ((300.0, 348.0), (387.0, 464.0), (779.0, 928.0))
BANDWIDTH = 270.8


def frequency(value):
    try:
        number = float(value)
        if not math.isfinite(number) or not any(low <= number <= high for low, high in BANDS):
            raise ValueError
        return number
    except (TypeError, ValueError) as error:
        raise wifi.WifiError("Use 300–348, 387–464, or 779–928 MHz") from error


def hat_key():
    value = wifi.read_setting("subghz-hat") or "rf2-v1"
    if value not in HATS:
        raise wifi.WifiError("Select a Sub-Ghz hat profile")
    return value


def configured_frequency():
    return frequency(wifi.read_setting("subghz-frequency") or "433.92")


def configured_threshold():
    try:
        value = int(wifi.read_setting("subghz-threshold") or "-70")
        if not -110 <= value <= -20:
            raise ValueError
        return value
    except ValueError as error:
        raise wifi.WifiError("Use a threshold from -110 to -20 dBm") from error


def pin_state(pin):
    output = subprocess.check_output(["pinctrl", "get", str(pin)], text=True, timeout=3)
    match = re.match(r"\s*\d+:\s+(ip|op|a[0-9])\s+(pu|pd|pn|--)\s+.*?\|\s+(hi|lo)", output)
    if not match:
        raise wifi.WifiError(f"Could not read GPIO{pin} configuration")
    mode, pull, level = match.groups()
    return (mode, *(("dh" if level == "hi" else "dl",) if mode == "op" else ()),
            *((pull,) if pull != "--" else ()))


def set_pin(pin, *values):
    subprocess.run(["pinctrl", "set", str(pin), *values], capture_output=True, check=True, timeout=3)


class CC1101:
    def __init__(self, spi, lines, hat, value_type):
        self.spi, self.lines, self.hat, self.value_type = spi, lines, hat, value_type
        self.version = None

    def transfer(self, data):
        if self.hat.cs is not None:
            self.lines.set_value(self.hat.cs, self.value_type.INACTIVE)
        try:
            return self.spi.xfer2(list(data))
        finally:
            if self.hat.cs is not None:
                self.lines.set_value(self.hat.cs, self.value_type.ACTIVE)

    def read(self, register):
        return self.transfer((register | (0xC0 if register >= 0x30 else 0x80), 0))[1]

    def write(self, register, value):
        self.transfer((register, value))

    def strobe(self, instruction):
        if instruction not in (0x30, 0x33, 0x34, 0x36, 0x39, 0x3A, 0x3D):
            raise wifi.WifiError("Unsupported monitor command")
        self.transfer((instruction,))

    def probe(self):
        identities = [(self.read(0x30), self.read(0x31)) for _ in range(3)]
        if len(set(identities)) != 1 or identities[0][0] != 0 or identities[0][1] not in (0x04, 0x14):
            raise wifi.WifiError("CC1101 not detected. Check the hat profile and set its switch to CC1101.")
        self.version = identities[0][1]

    def initialize(self):
        # After SPWD, CS must stay low until the crystal is ready to accept SPI.
        if self.hat.cs is None:
            set_pin(7, "op", "dl")
        else:
            self.lines.set_value(self.hat.cs, self.value_type.INACTIVE)
        try:
            time.sleep(0.005)
            self.strobe(0x3D)
        finally:
            if self.hat.cs is None:
                set_pin(7, "op", "dh")
            else:
                self.lines.set_value(self.hat.cs, self.value_type.ACTIVE)
        self.probe()
        self.strobe(0x30)
        time.sleep(0.15)
        # Direct asynchronous receive, with no sync-word or packet-format requirement.
        registers = {
            0x00: 0x2E, 0x01: 0x2E, 0x02: 0x2E, 0x03: 0x47,
            0x06: 0xFF, 0x07: 0x00, 0x08: 0x32, 0x09: 0,
            0x0B: 0x06, 0x0C: 0, 0x10: 0x67, 0x11: 0x83, 0x12: 0,
            0x13: 0x22, 0x14: 0xF8, 0x15: 0x15, 0x17: 0x0C, 0x18: 0x18,
            0x19: 0x16, 0x1A: 0x6C, 0x1B: 0x43, 0x1C: 0x40, 0x1D: 0x91,
            0x21: 0x56, 0x22: 0x10, 0x23: 0xE9, 0x24: 0x2A,
            0x25: 0, 0x26: 0x1F, 0x2C: 0x81, 0x2D: 0x35, 0x2E: 0x09,
        }
        for register, value in registers.items():
            self.write(register, value)

    def tune(self, mhz):
        mhz = frequency(mhz)
        self.strobe(0x36)
        if self.hat.band_switch is not None:
            self.lines.set_value(self.hat.band_switch,
                                 self.value_type.ACTIVE if mhz >= 779 else self.value_type.INACTIVE)
            # Stock cap uses the CC1101 GDO2 constant output to select its RF path.
            self.write(0x00, 0x2F if mhz < 387 else 0x6F)
        value = round(mhz * 65536 / 26)
        for register, shift in ((0x0D, 16), (0x0E, 8), (0x0F, 0)):
            self.write(register, (value >> shift) & 255)
        readback = (self.read(0x0D) << 16) | (self.read(0x0E) << 8) | self.read(0x0F)
        if readback != value:
            raise wifi.WifiError("CC1101 frequency verification failed")
        self.strobe(0x33)
        time.sleep(0.015)
        self.strobe(0x34)
        time.sleep(0.025)
        if self.read(0x35) & 31 != 13:
            raise wifi.WifiError("CC1101 could not enter receive mode")

    def sample(self):
        if self.read(0x35) & 31 != 13:
            raise wifi.WifiError("CC1101 left receive mode")
        raw = self.read(0x34)
        return (raw - 256 if raw >= 128 else raw) / 2 - 74

    def stop(self):
        self.strobe(0x36)
        self.strobe(0x39)


@contextmanager
def receiver(profile):
    try:
        import gpiod
        from gpiod.line import Direction, Value
        from spidev import SpiDev
    except ImportError as error:
        raise wifi.WifiError("CC1101 requires python3-libgpiod and python3-spidev") from error
    hat = HATS[profile]
    wifi.DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with ExitStack() as cleanup:
            lock = cleanup.enter_context((wifi.DATA / "subghz.lock").open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise wifi.WifiError("Sub-Ghz Monitor is already running") from error

            states = {}
            def restore():
                for pin, state in states.items():
                    with suppress(OSError, subprocess.SubprocessError, wifi.WifiError):
                        set_pin(pin, *state)
            cleanup.callback(restore)
            outputs = dict(hat.outputs)
            if hat.cs is not None:
                outputs[hat.cs] = 1
            if hat.band_switch is not None:
                outputs[hat.band_switch] = 0
            if hat.power is not None:
                outputs[hat.power] = 1
            for pin in (*outputs, *hat.inputs):
                if pin != hat.power:
                    states[pin] = pin_state(pin)
            if hat.cs is None:
                states[7] = pin_state(7)
            config = {pin: gpiod.LineSettings(direction=Direction.OUTPUT,
                       output_value=Value.ACTIVE if value else Value.INACTIVE) for pin, value in outputs.items()}
            config.update({pin: gpiod.LineSettings(direction=Direction.INPUT) for pin in hat.inputs})
            lines = cleanup.enter_context(gpiod.request_lines("/dev/gpiochip0", consumer="zerogotchi-subghz", config=config))
            rail = Path("/sys/class/leds/ext_5v_out/brightness")
            rail.write_text("1")
            time.sleep(0.15)
            spi = SpiDev(0, 1)
            old_mode, old_no_cs, old_speed = spi.mode, spi.no_cs, spi.max_speed_hz
            def close_spi():
                with suppress(OSError):
                    spi.mode, spi.no_cs, spi.max_speed_hz = old_mode, old_no_cs, old_speed
                spi.close()
            cleanup.callback(close_spi)
            spi.mode, spi.max_speed_hz = 0, 500_000
            spi.no_cs = hat.cs is not None
            if hat.cs == 15:
                states[7] = pin_state(7)
                set_pin(7, "ip")  # RF2 v2 puts GDO0 on the kernel's default CS pin.
            radio = CC1101(spi, lines, hat, Value)
            radio.initialize()
            cleanup.callback(lambda: stop_quietly(radio))
            yield radio
    except (OSError, subprocess.SubprocessError) as error:
        raise wifi.WifiError("CC1101 access failed. Close other radio apps and check cap power/permissions.") from error


def stop_quietly(radio):
    with suppress(OSError, wifi.WifiError):
        radio.stop()


@dataclass(frozen=True)
class Status:
    phase: str
    frequency: float
    threshold: int
    rssi: float | None = None
    peak: float | None = None
    floor: float | None = None
    events: int = 0
    samples: int = 0
    elapsed: int = 0
    trace: tuple = ()


def monitor(profile, mhz, threshold, cancel, pause, report):
    mhz = frequency(mhz)
    status = Status("Starting", mhz, threshold)
    history = deque(maxlen=140)
    active = False
    started = time.monotonic()
    next_report = 0
    with receiver(profile) as radio:
        radio.tune(mhz)
        while not cancel.is_set():
            if pause.is_set():
                radio.strobe(0x36)
                report(replace(status, phase="Paused", elapsed=int(time.monotonic() - started)))
                if not wifi.wait_while_paused(cancel, pause, lambda: None):
                    break
                radio.tune(mhz)
                active = False
            strength = radio.sample()
            event = not active and strength >= threshold
            if strength >= threshold:
                active = True
            elif strength < threshold - 3:
                active = False
            history.append(strength)
            status = Status("Listening", mhz, threshold, strength,
                            max(status.peak if status.peak is not None else strength, strength),
                            min(status.floor if status.floor is not None else strength, strength),
                            status.events + int(event), status.samples + 1,
                            int(time.monotonic() - started), tuple(history))
            now = time.monotonic()
            if now >= next_report:
                report(status)
                next_report = now + 0.15
            cancel.wait(0.04)
    return replace(status, phase="Stopped", elapsed=int(time.monotonic() - started))
