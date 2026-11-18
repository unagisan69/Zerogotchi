"""Private circular zones for location-bearing survey observations."""

from dataclasses import dataclass
import json
import math

import wifi


@dataclass(frozen=True)
class Zone:
    latitude: float
    longitude: float
    radius: float

    def contains(self, point):
        lat, other_lat = map(math.radians, (self.latitude, point.latitude))
        delta_lat = other_lat - lat
        delta_lon = math.radians(point.longitude - self.longitude)
        a = (math.sin(delta_lat / 2) ** 2
             + math.cos(lat) * math.cos(other_lat) * math.sin(delta_lon / 2) ** 2)
        distance = 12_742_017.6 * math.asin(math.sqrt(max(0.0, min(1.0, a))))
        return distance <= self.radius + point.accuracy


def parse_zone(value):
    try:
        latitude, longitude, radius = map(float, value.split(","))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180
                and math.isfinite(radius) and radius > 0):
            raise ValueError
        return Zone(latitude, longitude, radius)
    except ValueError as error:
        raise wifi.WifiError("Use latitude, longitude, radius in meters") from error


def zones():
    raw = wifi.read_setting("survey-private-zones")
    if not raw:
        return []
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError
        return [parse_zone(value) for value in values]
    except (ValueError, wifi.WifiError) as error:
        raise wifi.WifiError("Fix Private Zones in Settings") from error


def save_zones(values):
    wifi.save_setting("survey-private-zones", json.dumps(
        [f"{zone.latitude},{zone.longitude},{zone.radius}" for zone in values]
    ))


def private(point, configured):
    return any(zone.contains(point) for zone in configured)
