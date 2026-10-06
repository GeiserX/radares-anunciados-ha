"""Keep Home Assistant zones equal to the radar list.

Every radar becomes a *passive* zone: the iOS companion app monitors it and
fires ``ios.zone_entered``, but a person inside it stays ``not_home`` instead of
taking the zone's name, so existing presence automations don't change.

The iOS app monitors at most 20 regions and itself keeps the 20 zones nearest
to its last location, re-choosing on every location event
(``ZoneManagerRegionFilter`` in home-assistant/iOS). So we load the radars, up to
``MAX_ZONES``, and let the phone pick. Zones under 100 m radius cost the app
three regions each, so radii here are never below 100 m.

The app stores zones only while it is open on screen, and only on a zone or
person state change at least 15 s after the last one it stored. So after a change
we notify the phones (tapping it opens the app), and 20 s later touch one zone
again (``nudge``) so an app already open stores the whole set.

A street whose period ended keeps its zones with the dormant icon, which the
blueprint does not alert on. Only the icon changes, in place: a delete and
create would cost the phone a region it already monitors.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from itertools import count

import websockets

from .geo import distance_m
from .model import REPORTED, Radar
from .speed import MIN_RADIUS_M

log = logging.getLogger(__name__)

# A zone is ours only if it has one of these icons AND its name starts with
# "Radar". Nothing else is ever updated or deleted.
ICON = "mdi:camera-timer"  # a radar in force: the blueprint alerts on it
DORMANT_ICON = "mdi:camera-off"  # a street whose period ended: kept, silent
ICONS = (ICON, DORMANT_ICON)
MAX_ZONES = 1000  # RADARES_MAX_ZONES; the iOS app handles about 1,000 safely
CALL_TIMEOUT_S = 60  # a Home Assistant that pings but never answers must not hang the run
NUDGE_STEP = 1e-7  # degrees (1 cm): below the 6 decimals a plan compares


@dataclass(frozen=True)
class ZoneSpec:
    name: str
    latitude: float
    longitude: float
    radius: float
    icon: str = ICON

    @classmethod
    def from_radar(cls, r: Radar) -> ZoneSpec:
        # The alert's title is the zone name: put the limit where the driver reads it.
        name = f"{r.name} (límite {r.maxspeed})" if r.maxspeed else r.name
        return cls(
            name,
            round(r.lat, 6),
            round(r.lon, 6),
            float(max(r.radius_m, MIN_RADIUS_M)),
            ICON if r.active else DORMANT_ICON,
        )

    @classmethod
    def from_zone(cls, z: dict) -> ZoneSpec:
        return cls(
            z["name"],
            round(z["latitude"], 6),
            round(z["longitude"], 6),
            float(z["radius"]),
            z.get("icon", ""),
        )

    def place(self) -> tuple[str, float, float, float]:
        """Everything but the icon: two specs with the same place are one zone,
        whose icon can change in place."""
        return self.name, self.latitude, self.longitude, self.radius


def is_ours(zone: dict) -> bool:
    return zone.get("icon") in ICONS and str(zone.get("name", "")).startswith("Radar")


@dataclass
class Plan:
    create: list[ZoneSpec]
    delete: list[str]  # zone ids
    keep: int  # zones left as they are or only given another icon
    update: list[tuple[str, str]] = field(default_factory=list)  # (zone id, new icon)
    left_out: int = 0  # radars over RADARES_MAX_ZONES, without a zone

    @property
    def changed(self) -> bool:
        return bool(self.create or self.delete or self.update)


def zoned(radars: list[Radar]) -> list[Radar]:
    """The radars that may become zones: all but the places people report and no
    source publishes (``REPORTED``), which stay in the feed and on the map."""
    return [r for r in radars if r.kind != REPORTED]


def select(
    radars: list[Radar], cap: int, home: tuple[float, float] | None
) -> tuple[list[Radar], int]:
    """At most ``cap`` radars, and how many were left out.

    In order: streets of a list in force (they change weekly and matter most),
    then fixed and section radars nearest to home, then the places where fines
    show a mobile radar stood on several days, then the circles along stretches
    where mobile radars may stand, each nearest first, then dormant streets, the
    most recently announced first. A fines place comes before a stretch circle:
    one zone marks a spot where a radar did stand, while a stretch takes many
    circles for kilometres where one only may. Never fails for being over the cap."""
    if len(radars) <= cap:
        return radars, 0

    def nearest_first(rs: list[Radar]) -> list[Radar]:
        if home is None:
            return sorted(rs, key=lambda r: r.id)
        return sorted(rs, key=lambda r: (distance_m(home, (r.lat, r.lon)), r.id))

    listed = sorted((r for r in radars if r.active and r.valid_to), key=lambda r: r.id)
    standing = [r for r in radars if r.active and not r.valid_to]
    later = ("mobile_recurring", "mobile_stretch")
    fixed = nearest_first([r for r in standing if r.kind not in later])
    recurring = nearest_first([r for r in standing if r.kind == "mobile_recurring"])
    stretch = nearest_first([r for r in standing if r.kind == "mobile_stretch"])
    dormant = sorted(
        (r for r in radars if not r.active),
        key=lambda r: (-(r.valid_to.toordinal() if r.valid_to else 0), r.id),
    )
    kept = (listed + fixed + recurring + stretch + dormant)[:cap]
    return kept, len(radars) - len(kept)


def plan(existing: list[dict], radars: list[Radar]) -> Plan:
    wanted: dict[tuple, ZoneSpec] = {}
    for spec in sorted((ZoneSpec.from_radar(r) for r in radars), key=lambda s: s.icon != ICON):
        wanted.setdefault(spec.place(), spec)  # one spot, active and dormant: active wins
    have: dict[tuple, str] = {}
    delete: list[str] = []
    update: list[tuple[str, str]] = []
    for z in existing:
        if not is_ours(z):
            continue
        spec = ZoneSpec.from_zone(z)
        key = spec.place()
        if key in wanted and key not in have and z.get("passive") is True:
            have[key] = z["id"]
            if spec.icon != wanted[key].icon:
                # Same zone, other icon: update in place. A delete and create
                # would cost the phone the region it already monitors.
                update.append((z["id"], wanted[key].icon))
        else:
            # stale, a duplicate of one we keep, or edited to a normal zone
            # (a radar zone that sets a person's state breaks presence)
            delete.append(z["id"])
    create = sorted(
        (wanted[k] for k in wanted.keys() - have.keys()),
        key=lambda s: (s.name, s.latitude, s.longitude),
    )
    return Plan(create=create, delete=sorted(delete), keep=len(have), update=sorted(update))


def nudged_latitude(latitude: float) -> float:
    """A latitude 1 cm off ``latitude`` that rounds to the same 6 decimals, and
    back again on the next call, so repeated nudges never drift."""
    canonical = round(latitude, 6)
    return canonical if abs(latitude - canonical) > NUDGE_STEP / 2 else canonical + NUDGE_STEP


def websocket_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base.removeprefix("https://") + "/api/websocket"
    if base.startswith("http://"):
        return "ws://" + base.removeprefix("http://") + "/api/websocket"
    return base


class HomeAssistant:
    def __init__(self, base_url: str, token: str):
        self.url = websocket_url(base_url)
        self.token = token
        self._ids = count(1)
        self._ws = None

    async def __aenter__(self) -> HomeAssistant:
        self._ws = await websockets.connect(self.url, max_size=16 * 1024 * 1024)
        hello = json.loads(await self._ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected hello from Home Assistant: {hello}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth = json.loads(await self._ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant refused the token: {auth.get('message', auth)}")
        return self

    async def __aexit__(self, *exc) -> None:
        await self._ws.close()

    async def call(self, msg_type: str, **payload) -> object:
        msg_id = next(self._ids)
        await self._ws.send(json.dumps({"id": msg_id, "type": msg_type, **payload}))
        # One deadline for the whole wait: unrelated messages must not reset it.
        async with asyncio.timeout(CALL_TIMEOUT_S):
            while True:
                reply = json.loads(await self._ws.recv())
                if reply.get("id") != msg_id or reply.get("type") != "result":
                    continue
                if not reply.get("success"):
                    raise RuntimeError(f"{msg_type} failed: {reply.get('error')}")
                return reply.get("result")

    async def home(self) -> tuple[float, float] | None:
        """Where zone.home is: Home Assistant's configured location."""
        config = await self.call("get_config")
        try:
            return float(config["latitude"]), float(config["longitude"])
        except (KeyError, TypeError, ValueError):
            return None

    async def sync(
        self, radars: list[Radar], dry_run: bool = False, max_zones: int = MAX_ZONES
    ) -> Plan:
        existing = await self.call("zone/list")
        radars = zoned(radars)
        left_out = 0
        if len(radars) > max_zones:
            radars, left_out = select(radars, max_zones, await self.home())
            log.warning(
                "%d radars over the %d-zone cap (RADARES_MAX_ZONES) get no zone",
                left_out,
                max_zones,
            )
        todo = plan(existing, radars)
        todo.left_out = left_out
        log.info(
            "zones: %d kept (%d with a new icon), %d to create, %d to delete",
            todo.keep,
            len(todo.update),
            len(todo.create),
            len(todo.delete),
        )
        if dry_run:
            return todo
        for zone_id in todo.delete:
            await self.call("zone/delete", zone_id=zone_id)
        for zone_id, icon in todo.update:
            await self.call("zone/update", zone_id=zone_id, icon=icon)
        for spec in todo.create:
            await self.call(
                "zone/create",
                name=spec.name,
                latitude=spec.latitude,
                longitude=spec.longitude,
                radius=spec.radius,
                passive=True,
                icon=spec.icon,
            )
        return todo

    async def nudge(self) -> str | None:
        """Move one of our zones by 1 cm (or back), a real state change.

        The iOS app stores zones only on a zone or person state change, and drops
        a change that comes within 15 s of the last one it stored: a sync's burst
        lands only its first zone. One more change a while later makes an open app
        store the whole set. 1 cm is under the 6 decimals a plan compares, so the
        next sync sees no difference. Returns the zone id, None if we have none."""
        ours = sorted(
            (z for z in await self.call("zone/list") if is_ours(z)), key=lambda z: z["id"]
        )
        if not ours:
            return None
        zone = ours[0]
        await self.call(
            "zone/update", zone_id=zone["id"], latitude=nudged_latitude(zone["latitude"])
        )
        return zone["id"]

    async def notify(self, targets: list[str], title: str, message: str) -> None:
        for target in targets:
            service = target.removeprefix("notify.")
            await self.call(
                "call_service",
                domain="notify",
                service=service,
                service_data={"title": title, "message": message},
            )
