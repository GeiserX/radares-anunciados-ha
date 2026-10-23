"""The speed limit of the road under a radar whose source publishes none.

DGT fixed and section radars, and OpenStreetMap cameras without ``maxspeed``,
come with no limit, so their zones fall back on the road class (``speed.py``).
This lookup asks OpenStreetMap instead: every drivable way within ``AROUND_M``
of each radar, many radars per Overpass query (the ~800 DGT points of Spain
cost 3), and keeps the ``maxspeed`` of the way the radar is on:

- the radar's road, when its name gives one ("A-7") and a way near it has that
  ``ref``; with none, only a carriageway the radar is on (``TIE_M``) of a class
  that road could be (``CARRIAGEWAY``): never a slip road, a roundabout street
  or another road nearby;
- a main carriageway before a service road or a slip road;
- of those, the nearest. Its limit, or nothing: a limit is never borrowed from
  another road, nor from the other carriageway.

``maxspeed`` values that are not one number in km/h: "55 mph" is converted,
"ES:motorway", "ES:rural" and "ES:zoneNN" are Spain's implicit limits, and
anything that is no single limit ("ES:urban", "none", "walk", "80;90", a
``maxspeed:forward`` that differs from ``:backward`` on a two-way road) gives
none: a wrong limit in the zone's name is worse than none.

Every answer, a limit or none, is kept per radar position in the cache folder
and asked again after 30 days, so a run asks only for radars it has not seen or
whose answer is that old. An answer is replaced only by a query that worked: a
failed query (an error, or Overpass's "remark" of a timeout or out of memory)
keeps the old answer, leaves a new radar without a limit (the road fallback
sizes it), caches nothing and is retried next run. A zone's name and radius
follow its limit, so a limit that came and went with Overpass's health would
delete and recreate the zone.

Data (c) OpenStreetMap contributors, ODbL 1.0. A radar given a limit here
credits OpenStreetMap in its attribution as well as its own source.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import replace

from . import net
from .model import REPORTED, Radar

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Added to the attribution of a radar whose limit came from here: the feed then
# carries OpenStreetMap data even when no OSM camera is in it.
ATTRIBUTION = "límite de velocidad: © OpenStreetMap contributors (ODbL 1.0)"
AROUND_M = 30  # how far from a radar its road may be; a dual carriageway's two halves fit
TIE_M = 5  # this close, the radar is on the way (and two pieces of one road meet)
BATCH = 250  # radars per Overpass query
MAX_AGE_S = 30 * 86_400
CACHE_FILE = "osm_limits.json"
VERSION = 1  # bump when the cached answer changes meaning; older entries are asked again

# highway classes: a carriageway, and a slip road or service road beside one
MAIN = tuple("motorway trunk primary secondary tertiary unclassified residential".split())
MAIN += ("living_street", "road")
MINOR = tuple("motorway_link trunk_link primary_link secondary_link tertiary_link".split())
MINOR += ("service",)
_DRIVABLE = "|".join(MAIN + MINOR)
# what an interurban road named in a DGT radar can be in OSM, when its ref is missing
CARRIAGEWAY = ("motorway", "trunk", "primary", "secondary")

# "A-7", "N-121-A", "CG-1.5", "Ma-13", "N-II" in a radar's name
_ROAD = re.compile(
    r"\b([A-Z]{1,3}-(?:\d{1,4}(?:\.\d{1,2})?(?:-?[A-Z])?|[IV]{1,3}))\b", re.IGNORECASE
)
# OSM writes the old national roads in Roman numerals ("N-I"); DGT in digits ("N-1")
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6"}
_KMH = re.compile(r"(\d{1,3})\s*(km/h|kmh|kph)?")
_MPH = re.compile(r"(\d{1,3})\s*mph")
# Spain's implicit limits, as OSM tags them. "ES:urban" is not here: since 2021
# an urban road is 20, 30 or 50 by its lanes, so the tag alone doesn't say.
_IMPLICIT = {"ES:motorway": 120, "ES:rural": 90}
_ZONE = re.compile(r"ES:zone(\d{2})")
_SKIP = ("mobile_announced", REPORTED)  # kinds never looked up

Point = tuple[float, float]


def road_of(radar: Radar) -> str | None:
    """'A-7' from 'Radar fijo A-7 km 580.3 (sentido ALMERIA)'; None without a road."""
    match = _ROAD.search(radar.name)
    return _norm(match.group(1)) if match else None


def _norm(ref: str) -> str:
    """One spelling per road: "N-I" and "N-1" give "N1"; "A-431a", "N-121-A" and
    "N-344A" give their road, "A431", "N121", "N344" (the letter marks an old
    stretch or a variant of the same road)."""
    ref = ref.replace(" ", "").upper()
    prefix, _, number = ref.partition("-")
    number = _ROMAN.get(number, number)
    return re.sub(r"(?<=\d)-?[A-Z]$", "", prefix + number.replace("-", ""))


def kmh(value: str | None) -> int | None:
    """km/h from one OSM ``maxspeed`` value, or None when it is no single limit:
    "90", "90 km/h", "55 mph", "ES:rural", "ES:zone30" give one; "none", "walk",
    "signals", "ES:urban", "80;90" and "50|30" don't."""
    if not value:
        return None
    value = value.strip()
    if value in _IMPLICIT:
        return _IMPLICIT[value]
    for pattern, factor in ((_KMH, 1.0), (_MPH, 1.609344)):
        match = pattern.fullmatch(value)
        if match:
            speed = round(int(match.group(1)) * factor)
            return speed if 0 < speed <= 150 else None
    match = _ZONE.fullmatch(value)
    return int(match.group(1)) if match else None


def way_kmh(tags: dict[str, str]) -> int | None:
    """The limit of a way for whoever drives it: ``maxspeed``, or on a way with
    no plain one, ``maxspeed:forward`` and ``:backward`` when they agree (or the
    forward one on a one-way road)."""
    if "maxspeed" in tags:
        return kmh(tags["maxspeed"])
    forward, backward = kmh(tags.get("maxspeed:forward")), kmh(tags.get("maxspeed:backward"))
    if tags.get("oneway") == "yes" or tags.get("highway") == "motorway":
        return forward
    return forward if forward == backward else None


def query(points: list[Point]) -> str:
    """One Overpass query for the drivable ways around every point."""
    parts = "".join(
        f'way["highway"~"^({_DRIVABLE})$"](around:{AROUND_M},{lat:.6f},{lon:.6f});'
        for lat, lon in points
    )
    return f"[out:json][timeout:180];({parts});out tags geom;"


def _ways(payload: bytes) -> list[tuple[dict[str, str], list[Point]]]:
    """The ways in an Overpass answer. Raises on an answer that is not whole:
    Overpass reports a timeout or running out of memory as HTTP 200 with a
    ``remark`` and the elements it had so far, often none."""
    data = json.loads(payload)
    if not isinstance(data, dict) or "remark" in data or "elements" not in data:
        remark = data.get("remark") if isinstance(data, dict) else None
        raise ValueError(f"incomplete Overpass answer: {remark or 'no elements'}")
    out = []
    for el in data["elements"]:
        if el.get("type") == "way" and el.get("geometry"):
            out.append((el.get("tags", {}), [(g["lat"], g["lon"]) for g in el["geometry"]]))
    return out


def _to_line_m(p: Point, line: list[Point]) -> float:
    """Metres from ``p`` to a polyline, on a flat projection around ``p``
    (exact enough within a few hundred metres)."""
    k = 111_320.0
    kx = k * math.cos(math.radians(p[0]))
    xy = [((lon - p[1]) * kx, (lat - p[0]) * k) for lat, lon in line]
    best = math.inf
    for (ax, ay), (bx, by) in zip(xy, xy[1:] or xy, strict=False):
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        t = 0.0 if span == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / span))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def choose(
    point: Point, road: str | None, ways: list[tuple[dict[str, str], list[Point]]]
) -> int | None:
    """The limit of the way the radar at ``point`` is on, or None."""
    near = []
    for tags, line in ways:
        if not _boxed(point, line):
            continue
        d = _to_line_m(point, line)
        if d <= AROUND_M:
            near.append((d, tags))
    if road:
        # The radar's road. Without it near: a carriageway under the radar may
        # be that road untagged, or carry it under another ref (A-30 and A-7
        # share a carriageway; OSM tags one). A slip road, a roundabout street
        # or a way a few metres off is another road, whatever its ref.
        same = [(d, t) for d, t in near if road in _refs(t)]
        near = same or [(d, t) for d, t in near if d <= TIE_M and t.get("highway") in CARRIAGEWAY]
    main = [(d, t) for d, t in near if t.get("highway") in MAIN]
    near = sorted(main or near, key=lambda dt: dt[0])
    if not near:
        return None
    best_d, best = near[0]
    found = way_kmh(best)
    if found is None:
        # The radar sits where two pieces of the same road meet: the other piece.
        same_road = (best.get("highway"), best.get("ref"))
        for d, tags in near[1:]:
            if d - best_d > TIE_M:
                break
            if (tags.get("highway"), tags.get("ref")) == same_road and way_kmh(tags):
                return way_kmh(tags)
    return found


def _refs(tags: dict[str, str]) -> set[str]:
    return {_norm(r) for key in ("ref", "int_ref") for r in tags.get(key, "").split(";") if r}


def _boxed(p: Point, line: list[Point], pad: float = 0.001) -> bool:
    """Cheap test before the exact distance: ``p`` within a way's bounding box
    plus ~100 m."""
    lats = [q[0] for q in line]
    lons = [q[1] for q in line]
    return (
        min(lats) - pad <= p[0] <= max(lats) + pad
        and min(lons) - 2 * pad <= p[1] <= max(lons) + 2 * pad
    )


def _key(radar: Radar) -> str:
    return f"{radar.lat:.5f},{radar.lon:.5f},{road_of(radar) or ''}"


def _ask(points: list[Point]) -> bytes:
    # One try, a little over the query's own 180 s: an optional lookup must not
    # hold the sync for minutes of retries; the next run asks again.
    return net.get(OVERPASS_URL, data={"data": query(points)}, timeout=200, tries=1)


def _entry(value: object) -> bool:
    """A cached answer: [limit or None, time asked]."""
    return (
        isinstance(value, list)
        and len(value) == 2
        and (value[0] is None or type(value[0]) is int)
        and type(value[1]) in (int, float)
    )


def _load() -> dict[str, list]:
    """Every well-formed cached answer, however old; anything else is dropped
    and asked again."""
    path = net.cache_dir() / CACHE_FILE
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    limits = data.get("limits")
    if not isinstance(limits, dict):
        return {}
    return {k: v for k, v in limits.items() if _entry(v)}


def _save(cache: dict[str, list]) -> None:
    path = net.cache_dir() / CACHE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": VERSION, "limits": cache}))
        tmp.replace(path)
    except OSError as exc:  # no cache costs queries next run, not this run
        log.warning("could not save the OSM speed limits in %s: %s", path.parent, exc)


def fill(radars: list[Radar], now: float | None = None) -> list[Radar]:
    """``radars`` with ``maxspeed`` set from OpenStreetMap where it was None.
    Street circles keep theirs: their street's limit was read when they were
    placed; a report (``REPORTED``) gets no zone to size. Never raises for a
    failed lookup."""
    now = time.time() if now is None else now
    wanted = [r for r in radars if r.maxspeed is None and r.kind not in _SKIP]
    if not wanted:
        return radars
    cache = _load()
    keys = {_key(r) for r in wanted}

    def stale(key: str) -> bool:
        return now - cache[key][1] >= MAX_AGE_S

    # an old answer of a radar no longer here is not worth keeping
    cache = {k: v for k, v in cache.items() if k in keys or not stale(k)}
    todo: dict[str, Radar] = {}
    for r in wanted:
        if _key(r) not in cache or stale(_key(r)):
            todo.setdefault(_key(r), r)
    pending = list(todo.items())
    asked = 0
    for start in range(0, len(pending), BATCH):
        batch = pending[start : start + BATCH]
        try:
            ways = _ways(_ask([(r.lat, r.lon) for _, r in batch]))
        except Exception as exc:  # Overpass down or busy: the rest waits for the next run
            log.warning(
                "OSM speed limits: query failed (%s); %d radars keep their last answer"
                " or the road fallback",
                exc,
                len(pending) - start,
            )
            break
        asked += 1
        for key, r in batch:
            cache[key] = [choose((r.lat, r.lon), road_of(r), ways), now]
        _save(cache)
    out = []
    found = 0
    for r in radars:
        entry = cache.get(_key(r)) if r.maxspeed is None and r.kind not in _SKIP else None
        if entry and entry[0] is not None:
            found += 1
            credit = f"{r.attribution}; {ATTRIBUTION}" if r.attribution else ATTRIBUTION
            r = replace(r, maxspeed=entry[0], attribution=credit)
        out.append(r)
    log.info(
        "OSM speed limits: %d of %d radars without a limit got one (%d queries)",
        found,
        len(wanted),
        asked,
    )
    return out
