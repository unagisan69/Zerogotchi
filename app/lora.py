"""Receive-only SX1262 access for the Cardputer Zero LoRa cap."""

from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
import struct
import time

from gnss import RAIL
from wifi import WifiError


@dataclass(frozen=True)
class Profile:
    frequency: float
    bandwidth: float
    spreading: int
    coding: int
    sync: int
    preamble: int


BANDWIDTH_CODES = {62.5: 0x03, 125: 0x04, 250: 0x05, 500: 0x06}
RX_DONE, HEADER_ERROR, CRC_ERROR = 0x02, 0x20, 0x40


class Receiver:
    def __init__(self, spi, gpio, active):
        self.spi = spi
        self.gpio = gpio
        self.active = active

    def transfer(self, data):
        deadline = time.monotonic() + 0.35
        while self.gpio.get_value(22) == self.active:
            if time.monotonic() >= deadline:
                raise WifiError("LoRa cap did not answer (BUSY)")
            time.sleep(0.001)
        return bytes(self.spi.xfer2(list(data)))

    def write(self, opcode, *data):
        self.transfer((opcode, *data))

    def read(self, opcode, size, *prefix):
        return self.transfer((opcode, *prefix, 0, *([0] * size)))[-size:]

    def standby(self):
        self.write(0x80, 0x00)

    def initialize(self):
        self.standby()
        status = self.transfer((0xC0, 0))[1]
        if status & 0x70 != 0x20:
            raise WifiError("SX1262 did not respond. Check the LoRa cap.")
        self.write(0x97, 0x06, 0x00, 0x01, 0x40)  # DIO3 TCXO, 3.0 V, 5 ms
        self.write(0x89, 0x7F)  # Calibrate after enabling the TCXO.
        self.write(0x9D, 0x01)  # DIO2 controls the cap RF switch.
        self.write(0x8A, 0x01)  # LoRa packet type.
        self.write(0x8F, 0x00, 0x00)  # TX/RX buffer offsets.

    def tune(self, profile):
        self.standby()
        frequency = profile.frequency
        low = int(frequency // 4) - 1
        self.write(0x98, low, low + 3)  # Image calibration around the receive frequency.
        self.write(0x86, *struct.pack(">I", round(frequency * 1e6 * (1 << 25) / 32e6)))
        bw = BANDWIDTH_CODES[profile.bandwidth]
        ldro = int((1 << profile.spreading) / profile.bandwidth >= 16)
        self.write(0x8B, profile.spreading, bw, profile.coding - 4, ldro)
        self.write(0x8C, 0, profile.preamble, 0, 255, 1, 0)
        self.write(0x0D, 0x07, 0x40, (profile.sync & 0xF0) | 4, (profile.sync << 4 & 0xF0) | 4)
        self.write(0x08, 0, RX_DONE | HEADER_ERROR | CRC_ERROR, 0, 0, 0, 0, 0, 0)
        self.write(0x02, 0xFF, 0xFF)
        self.write(0x82, 0xFF, 0xFF, 0xFF)  # Continuous RX.

    def packet(self):
        flags = int.from_bytes(self.read(0x12, 2), "big")
        if not flags:
            return None
        self.write(0x02, flags >> 8, flags & 0xFF)
        if not flags & RX_DONE or flags & (HEADER_ERROR | CRC_ERROR):
            return None
        length, offset = self.read(0x13, 2)
        if not length:
            return None
        signal, _, _ = self.read(0x14, 3)
        return self.read(0x1E, length, offset), -signal / 2


@contextmanager
def open_receiver():
    try:
        import gpiod
        from gpiod.line import Direction, Value
        from spidev import SpiDev
    except ImportError as error:
        raise WifiError("Meshmapping radio packages are missing") from error
    try:
        with ExitStack() as cleanup:
            gpio = cleanup.enter_context(gpiod.request_lines(
                "/dev/gpiochip0", consumer="zerogotchi-mesh",
                config={22: gpiod.LineSettings(direction=Direction.INPUT),
                        26: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)},
            ))
            saved_power = RAIL.read_text().strip()
            if saved_power == "0":
                RAIL.write_text("1\n")
                cleanup.callback(RAIL.write_text, "0\n")
                time.sleep(0.15)

            spi = SpiDev(0, 1)
            cleanup.callback(spi.close)
            spi.max_speed_hz = 1_000_000
            spi.mode = 0
            gpio.set_value(26, Value.INACTIVE)
            time.sleep(0.002)
            gpio.set_value(26, Value.ACTIVE)
            time.sleep(0.02)

            receiver = Receiver(spi, gpio, Value.ACTIVE)
            receiver.initialize()

            def stop():
                with suppress(WifiError, OSError):
                    receiver.standby()
            cleanup.callback(stop)
            yield receiver
    except OSError as error:
        raise WifiError(f"LoRa cap unavailable: {error.strerror or error}") from error
