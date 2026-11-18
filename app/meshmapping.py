"""Passive mesh discovery and GPS-tagged surveys."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import struct
import tempfile
import time

import gnss
import lora
import survey_privacy
import wdgwars
import wifi


REGIONS = {
    "us": ("USA", lora.Profile(910.525, 62.5, 7, 5, 0x12, 8)),
    "eu": ("EU/UK", lora.Profile(869.618, 62.5, 8, 8, 0x12, 8)),
}


@dataclass(frozen=True)
class Node:
    id: str
    role: str
    name: str = ""
    coordinates: tuple | None = None
    direct: bool = False


@dataclass(frozen=True)
class Status:
    phase: str
    profile: str = ""
    meshcore: int = 0
    located: int = 0


@dataclass(frozen=True)
class Survey:
    status: Status
    records: tuple


def meshcore_node(frame):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not frame or frame[0] >> 2 != 4:
        return None
    route = frame[0] & 3
    index = 1 if route in (1, 2) else 5
    if len(frame) <= index:
        return None
    path = frame[index]
    width = (path >> 6) + 1
    if width == 4 or (path & 63) * width > 64:
        return None
    payload = frame[index + 1 + (path & 63) * width:]
    if not 100 <= len(payload) <= 132:
        return None
    key, signature, advert = payload[:32], payload[36:100], payload[100:]
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, payload[:36] + advert)
    except (ValueError, InvalidSignature):
        return None
    flags = advert[0] if advert else 0
    role = {1: "chat", 2: "repeater", 3: "room", 4: "sensor"}.get(flags & 15, "unknown")
    offset = 1 + (8 if flags & 0x10 else 0) + 2 * bool(flags & 0x20) + 2 * bool(flags & 0x40)
    if advert and len(advert) < offset:
        return None
    coordinates = None
    if flags & 0x10:
        lat, lon = (value / 1e6 for value in struct.unpack_from("<ii", advert, 1))
        if (lat or lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            coordinates = lat, lon
    name = advert[offset:].decode("utf-8", errors="replace") if flags & 0x80 else ""
    name = "".join(char for char in name if char.isprintable())
    return Node(key.hex(), role, name, coordinates, direct=not (path & 63))


def save_survey(records):
    folder = wifi.DATA / "mesh"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", prefix=".mesh-", dir=folder, delete=False,
                                     encoding="utf-8") as output:
        temporary = output.name
        try:
            json.dump(records, output, allow_nan=False)
            output.flush()
            final = folder / f"mesh-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{output.name[-8:]}.json"
            os.replace(temporary, final)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def run(region, position, cancel, report):
    _, profile = REGIONS[region]
    zones = survey_privacy.zones()
    nodes = set()
    records = {}
    listening = False

    def status(phase):
        return Status(phase, f"{profile.frequency:g} MHz", len(nodes), len(records))

    with lora.open_receiver() as radio:
        while not cancel.is_set():
            fix = position()
            if zones and (fix is None or survey_privacy.private(fix, zones)):
                if listening:
                    radio.standby()
                    listening = False
                report(status("Private" if fix else "Locating"))
                cancel.wait(0.5)
                continue
            if not listening:
                radio.tune(profile)
                listening = True
                report(status("Listening"))
            packet = radio.packet()
            if packet:
                payload, rssi = packet
                node = meshcore_node(payload)
                if node:
                    nodes.add(node.id)
                    old = records.get(node.id)
                    if fix and (old is None
                                or (node.coordinates and not old["advertised"])
                                or (node.direct and not old["direct"] and not old["advertised"])):
                        records[node.id] = {
                            "node_id": node.id, "node_type": node.role, "name": node.name,
                            "rssi": rssi, "first_seen": old["first_seen"] if old else
                            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                            "receiver": [fix.latitude, fix.longitude, fix.accuracy],
                            "advertised": node.coordinates, "direct": node.direct,
                        }
                    report(status("Listening"))
            cancel.wait(0.045)
    saved = tuple(records.values())
    if saved:
        save_survey(saved)
    return Survey(status("Stopped"), saved)


def upload_records(survey):
    zones = survey_privacy.zones()
    for row in survey.records:
        lat, lon, accuracy = row["receiver"]
        if survey_privacy.private(gnss.Fix(lat, lon, 0, accuracy, 0), zones):
            continue
        if row["advertised"]:
            lat, lon = row["advertised"]
            accuracy = 0
        elif not row["direct"]:
            continue
        if (lat or lon) and not survey_privacy.private(gnss.Fix(lat, lon, 0, accuracy, 0), zones):
            yield {
                **{key: row[key] for key in ("node_id", "node_type", "name", "rssi", "first_seen")},
                "lat": lat, "lon": lon, "type": "MESHCORE",
            }


def upload(survey):
    records = list(upload_records(survey))
    if not records:
        raise wifi.WifiError("No mapped MeshCore nodes available to upload")
    return wdgwars.upload_mesh(records)
