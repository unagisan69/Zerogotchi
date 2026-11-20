"""Follow APPLaunch's DarkTime and brightness settings."""

import json
from pathlib import Path
import time


class ScreenIdle:
    def __init__(self, config=None, backlight=None, clock=time.monotonic):
        self.config = config or Path.home() / ".config/cardputerzero/config.json"
        self.backlight = backlight or Path("/sys/class/backlight/backlight/brightness")
        self.clock = clock
        self.last_activity = clock()
        self.next_refresh = 0
        self.timeout = 30
        self.brightness = 40
        self.sleeping = self.owned = self.failed = False

    def tick(self):
        if self.failed:
            return
        now = self.clock()
        try:
            if now >= self.next_refresh:
                try:
                    settings = json.loads(self.config.read_text())
                    timeout = int(settings.get("dark_time", 30))
                    if timeout >= 0:
                        self.timeout = timeout
                    brightness = int(settings.get("brightness", self.brightness))
                    if brightness > 0:
                        self.brightness = brightness
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
                self.next_refresh = now + 2
            current = int(self.backlight.read_text())
            if current > 0:
                self.brightness = current
                self.sleeping = self.owned = False
            else:
                self.sleeping = True
            if self.timeout == 0:
                self.restore()
            elif not self.sleeping and now - self.last_activity >= self.timeout:
                self.backlight.write_text("0")
                self.sleeping = self.owned = True
        except (OSError, ValueError):
            self.failed = True

    def activity(self):
        self.last_activity = self.clock()
        if not self.sleeping:
            return False
        try:
            self.backlight.write_text(str(self.brightness))
            self.sleeping = self.owned = False
        except OSError:
            pass
        return True

    def restore(self):
        try:
            if self.owned and int(self.backlight.read_text()) == 0:
                self.backlight.write_text(str(self.brightness))
            self.sleeping = self.owned = False
        except (OSError, ValueError):
            pass
