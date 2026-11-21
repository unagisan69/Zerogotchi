"""CC1101 signal-strength monitoring on documented Cardputer cap pinouts."""

from collections import deque
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, replace
import fcntl
import json
import os
import secrets
import math
from pathlib import Path
import re
import subprocess
import struct
import threading
import time

import wifi


@dataclass(frozen=True)
class Hat:
    name: str
    cs: int
    inputs: tuple = ()
    outputs: tuple = ()
    band_switch: int | None = None
    power: int | None = None


HATS = {
    "rf2-v1": Hat("Kasiin RF 2in1 v1", 22, inputs=(23,)),
    "rf2-v2": Hat("Kasiin RF 2in1 v2", 15, inputs=(7,), outputs=((22, 1), (23, 0))),
    "pingequa": Hat("Pingequa CC1101 + NRF24", 15, inputs=(7,), outputs=((22, 1), (23, 0))),
    "m5-cap": Hat("M5Stack Cap CC1101", 7, inputs=(15,), outputs=((22, 1),),
                  band_switch=14, power=26),
}
PRESETS = (310.0, 315.0, 330.0, 390.0, 418.0, 430.0, 430.5, 433.92, 868.0, 915.0)
BANDS = ((300.0, 348.0), (387.0, 464.0), (779.0, 928.0))
BANDWIDTH = 270.8
# Keep app-owned LCD writes and radio requests ordered as well as kernel-arbitrated.
SPI_LOCK = threading.RLock()
TX_ACTIVE = threading.Event()


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


def kernel_cs_pin():
    path = Path("/sys/firmware/devicetree/base/soc/spi@7e204000/cs-gpios")
    try:
        data = path.read_bytes()
        cells = struct.unpack(f">{len(data) // 4}I", data)
        return cells[4] if len(cells) >= 6 and cells[0] == cells[3] and cells[5] & 1 else None
    except (OSError, ValueError, struct.error):
        return None


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
        self.frequency = None
        self.recoveries = 0
        self.unsteady_reads = 0
        self.last_bad_state = None
        self.recent_recoveries = deque()

    def transfer(self, data):
        with SPI_LOCK:
            return self.spi.xfer2(list(data))

    def read(self, register):
        return self.transfer((register | (0xC0 if register >= 0x30 else 0x80), 0))[1]

    def write(self, register, value):
        self.transfer((register, value))

    def strobe(self, instruction):
        if instruction not in (0x30, 0x33, 0x34, 0x35, 0x36, 0x39, 0x3A, 0x3B, 0x3D):
            raise wifi.WifiError("Unsupported monitor command")
        self.transfer((instruction,))

    def probe(self):
        identities = [(self.read(0x30), self.read(0x31)) for _ in range(3)]
        if len(set(identities)) != 1 or identities[0][0] != 0 or identities[0][1] not in (0x04, 0x14):
            raise wifi.WifiError("CC1101 not detected. Check the hat profile and set its switch to CC1101.")
        self.version = identities[0][1]

    def initialize(self):
        # The transfer delay keeps CS low during oscillator startup, with the
        # kernel holding the SPI bus so LCD traffic cannot enter this wakeup.
        with SPI_LOCK:
            self.spi.xfer2([0x3D], 500_000, 5_000)
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
        deadline = time.monotonic() + 0.25
        while self.state() != 13:
            if time.monotonic() >= deadline:
                raise wifi.WifiError("CC1101 could not enter receive mode")
            time.sleep(0.003)
        self.frequency = mhz

    def state(self):
        # TI SWRZ020E: asynchronously updated MARCSTATE needs consecutive matching reads.
        previous = self.read(0x35) & 31
        for _ in range(7):
            current = self.read(0x35) & 31
            if current == previous:
                return current
            self.unsteady_reads += 1
            previous = current
        return None

    def sample(self):
        state = self.state()
        if state is None:
            return None
        if state != 13:
            self.last_bad_state = state
            now = time.monotonic()
            while self.recent_recoveries and now - self.recent_recoveries[0] > 10:
                self.recent_recoveries.popleft()
            if len(self.recent_recoveries) >= 3:
                raise wifi.WifiError(
                    f"CC1101 RX remains unstable (state 0x{state:02x}). Check the hat connection and profile."
                )
            self.probe()
            self.strobe(0x36)
            time.sleep(0.003)
            self.strobe(0x3A)
            self.tune(self.frequency)
            self.recent_recoveries.append(now)
            self.recoveries += 1
            return None
        raw = self.read(0x34)
        return (raw - 256 if raw >= 128 else raw) / 2 - 74

    def stop(self):
        self.strobe(0x36)
        self.strobe(0x39)


@contextmanager
def receiver(profile, *, edges=False):
    try:
        import gpiod
        from gpiod.line import Direction, Value, Edge
        from spidev import SpiDev
    except ImportError as error:
        raise wifi.WifiError("CC1101 requires python3-libgpiod and python3-spidev") from error
    hat = HATS[profile]
    if kernel_cs_pin() != hat.cs:
        raise wifi.WifiError(
            f"Hat needs kernel SPI CS1 on GPIO{hat.cs}. Apply its boot configuration and restart. See the guide."
        )
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
            if hat.band_switch is not None:
                outputs[hat.band_switch] = 0
            if hat.power is not None:
                outputs[hat.power] = 1
            for pin in (*outputs, *hat.inputs):
                if pin != hat.power:
                    states[pin] = pin_state(pin)
            config = {pin: gpiod.LineSettings(direction=Direction.OUTPUT,
                       output_value=Value.ACTIVE if value else Value.INACTIVE) for pin, value in outputs.items()}
            config.update({pin: gpiod.LineSettings(direction=Direction.INPUT,
                           edge_detection=Edge.BOTH if edges else Edge.NONE) for pin in hat.inputs})
            lines = cleanup.enter_context(gpiod.request_lines("/dev/gpiochip0", consumer="zerogotchi-subghz", config=config, event_buffer_size=16384))
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
            spi.no_cs = False
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
    recoveries: int = 0


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
            if strength is None:
                status = replace(status, phase="Recovering", recoveries=radio.recoveries,
                                 elapsed=int(time.monotonic() - started))
                report(status)
                cancel.wait(0.04)
                continue
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
                            int(time.monotonic() - started), tuple(history), radio.recoveries)
            now = time.monotonic()
            if now >= next_report:
                report(status)
                next_report = now + 0.15
            cancel.wait(0.04)
    return replace(status, phase="Stopped", elapsed=int(time.monotonic() - started))


MAX_RECORD_US = 15_000_000
MAX_PULSES = 100_000
REPLAY_RATE = (256 + 147) * 2**9 * 26_000_000 / 2**28


def validate_record(data):
    try:
        mhz = frequency(data['frequency'])
        pulses = data['pulses']
        if data['format'] != 'zerogotchi-ook-v1' or not isinstance(pulses, list):
            raise ValueError
        if not 2 <= len(pulses) <= MAX_PULSES:
            raise ValueError
        if any(type(value) is not int or value == 0 for value in pulses):
            raise ValueError
        if sum(abs(value) for value in pulses) > MAX_RECORD_US:
            raise ValueError
        if not any(value > 0 for value in pulses):
            raise ValueError
        return {'format': 'zerogotchi-ook-v1', 'frequency': mhz, 'pulses': pulses}
    except (KeyError, TypeError, ValueError) as error:
        raise wifi.WifiError('Invalid or empty OOK recording (maximum 15 seconds).') from error


def recordings():
    folder = wifi.DATA / 'subghz'
    return sorted(folder.glob('*.json'), reverse=True) if folder.exists() else []


def load_record(path):
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError
        return validate_record(json.loads(path.read_text()))
    except (OSError, ValueError) as error:
        raise wifi.WifiError('Could not read this OOK recording.') from error


def save_record(data):
    data = validate_record(data)
    folder = wifi.DATA / 'subghz'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}_{data['frequency']:g}MHz_{secrets.token_hex(3)}.json"
    path = folder / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def record(profile, mhz, threshold, cancel, report):
    """Timestamp demodulated OOK edges in the kernel; reject any lost edges."""
    from gpiod import EdgeEvent
    pulses = []
    started = time.monotonic()
    previous = None
    level = 0
    sequence = None
    armed = False
    next_report = 0
    with receiver(profile, edges=True) as radio:
        radio.write(0x12, 0x30)  # ASK/OOK, no sync or Manchester.
        radio.write(0x10, 0x69)
        radio.write(0x11, 147)
        radio.write(0x1B, 0x07)  # OOK AGC settings.
        radio.write(0x1C, 0x00)
        radio.write(0x1D, 0x91)
        radio.write(0x02, 0x0D)  # GDO0 = asynchronous demodulated data.
        radio.tune(mhz)
        # Discard demodulator startup transitions queued during calibration.
        for _ in range(16):
            if not radio.lines.wait_edge_events(timeout=0):
                break
            radio.lines.read_edge_events(max_events=4096)
        while not cancel.is_set() and time.monotonic() - started < 15:
            strength = radio.sample()
            armed = armed or (strength is not None and strength >= threshold)
            if radio.recoveries:
                raise wifi.WifiError('Radio resynchronized during recording. Please record again.')
            if radio.lines.wait_edge_events(timeout=0.01):
                for event in radio.lines.read_edge_events(max_events=4096):
                    if sequence is not None and event.line_seqno != sequence + 1:
                        raise wifi.WifiError('Recording lost GPIO edges. Please record again.')
                    sequence = event.line_seqno
                    new_level = int(event.event_type == EdgeEvent.Type.RISING_EDGE)
                    stamp = event.timestamp_ns // 1000
                    if armed and previous is None and new_level:
                        previous, level = stamp, 1
                    elif previous is not None:
                        duration = stamp - previous
                        if duration > 0:
                            pulses.append(duration if level else -duration)
                        previous, level = stamp, new_level
                    if len(pulses) >= MAX_PULSES:
                        raise wifi.WifiError('Too many pulses/noise. Try a stronger, shorter signal.')
            now = time.monotonic()
            if now >= next_report:
                report(f"{mhz:g} MHz · OOK\n{len(pulses)} pulses · {int(now-started)} / 15s\n"
                       + ('Recording signal' if previous is not None else 'Waiting for signal above threshold'))
                next_report = now + .2
    if not pulses:
        raise wifi.WifiError('No OOK signal captured. Check frequency, threshold and remote.')
    # End at the last observed edge, without retaining a long idle/noise tail.
    return validate_record({'format': 'zerogotchi-ook-v1', 'frequency': mhz, 'pulses': pulses})


def encode_record(data):
    """Quantize against cumulative timestamps to avoid per-pulse timing drift."""
    data = validate_record(data)
    result = bytearray()
    elapsed = emitted = byte = count = 0
    for pulse in data['pulses']:
        elapsed += abs(pulse)
        target = round(elapsed * REPLAY_RATE / 1_000_000)
        for _ in range(target - emitted):
            byte = (byte << 1) | int(pulse > 0)
            count += 1
            if count == 8:
                result.append(byte)
                byte = count = 0
        emitted = target
    if count:
        result.append(byte << (8-count))
    result.extend(b'\0\0')
    return bytes(result)


def replay(profile, data, cancel, report):
    data = validate_record(data)
    payload = encode_record(data)
    with receiver(profile) as radio:
        radio.tune(data['frequency'])
        radio.strobe(0x36)
        radio.strobe(0x3B)
        radio.write(0x02, 0x2E)
        radio.write(0x08, 0x02)  # FIFO, infinite length, CRC/whitening off.
        radio.write(0x12, 0x30)  # OOK, no preamble/sync.
        radio.write(0x10, 0x69)
        radio.write(0x11, 147)
        radio.write(0x22, 0x11)  # OOK PATABLE entries 0/1.
        radio.transfer([0x7E, 0x00, 0x12])  # Off and low-power on.
        radio.transfer([0x7F, *payload[:64]])
        position = min(64, len(payload))
        started = time.monotonic()
        # LCD frames can hold the shared SPI bus longer than the TX FIFO.
        # Suppress new frames while streaming; the UI still polls Esc normally.
        with SPI_LOCK:
            TX_ACTIVE.set()
        try:
            if cancel.is_set():
                return 'Replay stopped.'
            radio.strobe(0x35)
            while not cancel.is_set():
                # TI errata: accept only consecutive matching TXBYTES reads.
                before = radio.read(0x3A)
                for _ in range(8):
                    amount = radio.read(0x3A)
                    if before == amount:
                        break
                    before = amount
                else:
                    raise wifi.WifiError('Unstable TX FIFO status; replay stopped.')
                if amount & 0x80:
                    if position != len(payload):
                        raise wifi.WifiError('Replay interrupted by FIFO underrun. Try a shorter recording.')
                    return 'Replay complete (one transmission).'
                remaining = amount & 0x7F
                if remaining > 64:
                    raise wifi.WifiError('Invalid TX FIFO status; replay stopped.')
                if position < len(payload) and remaining <= 32:
                    chunk = payload[position:position + 64-remaining]
                    radio.transfer([0x7F, *chunk])
                    position += len(chunk)
                if time.monotonic() - started > len(payload)*8/REPLAY_RATE + 2:
                    raise wifi.WifiError('Replay timed out; transmitter stopped.')
                cancel.wait(.002)
            return 'Replay stopped.'
        finally:
            try:
                radio.strobe(0x36)
                radio.strobe(0x3B)
            finally:
                TX_ACTIVE.clear()
