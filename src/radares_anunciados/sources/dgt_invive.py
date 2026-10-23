"""DGT stretches where mobile radars run (INVIVE), from the DGT NAP (DATEX II).

Dataset: https://nap.dgt.es/es/dataset/tramos-invive. The NAP page states the
licence as "Creative Commons Attribution", source and author "DGT", and links its
terms of use to https://www.dgt.es/contenido/aviso-legal/. About 1,330 stretches
of conventional road in the 43 provinces where DGT polices interurban roads,
each with two end points, the road and a km range; no speed limit.

Every stretch goes to the feed as a line. Zones are opt-in
(``RADARES_STRETCH_ZONES=on``, read by the CLI into ``Context.stretch_zones``) and
only for an explicit province list: circles
along the road, one at each end and the rest at most 1.8 radii apart, so every
metre of the road is within 0.9 radii of a centre. The radius is the speed rule
of a fixed radar. The road between the two
ends comes from OpenStreetMap ways carrying the stretch's road ref, one Overpass
query per province, cached for months. A failed or incomplete Overpass answer
fails the fetch, so the registry keeps the last good result (and its zones)
instead of moving them onto straight lines; such an answer is never cached.

A stretch whose road is not in the answer is drawn as a straight line and its
feature says so; its zones are only the two published end points, named so. A
line that does not fit the published km range (two points 20 km apart for a
150 m stretch, or the same point given as start and end of an 11 km one) gets no
zones at all, and its feature says that too.

The file declares ``xmlns:xsd="http:www.w3.org/2001/XMLSchema"`` (no ``//``).
No element uses that prefix and ElementTree does not check URIs, so it parses;
the fixture keeps the declaration as served.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import logging
import math
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import replace

from .. import net
from ..geo import distance_m
from ..model import Radar, SourceResult, Stretch
from ..provinces import PROVINCES, Box
from .base import Context, Source
from .osm import OVERPASS_URL

log = logging.getLogger(__name__)

KEY = "dgt_invive"
URL = "https://nap.dgt.es/datex2/dgt/PredefinedLocationsPublication/tramos_invive/content.xml"
ATTRIBUTION = "Dirección General de Tráfico (Creative Commons Attribution)"
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
LICENCE = (
    "Creative Commons Attribution (as stated on the NAP dataset page, no version given); "
    "terms of use https://www.dgt.es/contenido/aviso-legal/"
)
ZONES_ENV = "RADARES_STRETCH_ZONES"
GEOMETRY_MAX_AGE_S = 90 * 86_400  # roads barely move; the query changes when the roads do
OVERPASS_TIMEOUT_S = 180  # the query's own [timeout:]
OVERPASS_WAIT_S = OVERPASS_TIMEOUT_S + 30  # the client waits for Overpass to give up first
SNAP_M = 3000  # an end point further than this from every way of its road is not on it
BRIDGE_M = 500  # joins two ways of the road across an untagged roundabout or a gap
STRAIGHT_NOTE = " (línea recta: trazado de la carretera no encontrado)"
ENDS_NOTE = " (solo inicio y fin: trazado no encontrado)"
MISMATCH_NOTE = " (puntos publicados que no cuadran con los km: sin zonas)"

# DGT does not police interurban roads in Catalonia and the Basque Country.
COVERED = frozenset(PROVINCES) - {"01", "08", "17", "20", "25", "43", "48"}

_D = "{http://datex2.eu/schema/1_0/1_0}"
# What may reach the Overpass regex: letters, digits, '-' and '.' (escaped as
# [.]). That takes every ref in the file (N-IIa, ZA-P-1405, CG-2.1, EX-A2-R1)
# and nothing that could close the string or change the regex.
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,19}$")
Point = tuple[float, float]


def _fold(text: str) -> str:
    """'VALÈNCIA/VALENCIA' and 'València / Valencia' to the same key."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s*/\s*", "/", plain.strip().upper())


def _province_codes() -> dict[str, str]:
    """Every name and part of a bilingual name in ``PROVINCES``, folded, to its code."""
    out: dict[str, str] = {}
    for code, (name, _) in PROVINCES.items():
        folded = _fold(name)
        out[folded] = code
        for part in folded.split("/"):
            out[part] = code
    # The file writes the community's province without the community's prefix.
    out |= {"MADRID": "28", "MURCIA": "30"}
    return out


_CODES = _province_codes()


def province_code(name: str | None) -> str | None:
    """INE code of a province as the file names it ('ALACANT/ALICANTE'), or None."""
    return _CODES.get(_fold(name)) if name else None


def _text(el: ET.Element | None, path: str) -> str | None:
    found = el.find(path) if el is not None else None
    return found.text.strip() if found is not None and found.text else None


def _point(el: ET.Element | None) -> Point | None:
    lat = _text(el, f"{_D}pointCoordinates/{_D}latitude")
    lon = _text(el, f"{_D}pointCoordinates/{_D}longitude")
    return (float(lat), float(lon)) if lat and lon else None


def _km(ref: ET.Element | None) -> float | None:
    dist = _text(ref, f"{_D}referencePointDistance")
    return round(float(dist) / 1000, 3) if dist else None


def _span(road: str, km_from: float | None, km_to: float | None) -> str:
    if km_from is None or km_to is None:
        return road
    low, high = sorted((km_from, km_to))
    return f"{road} km {low:g}-{high:g}"


def parse(xml: bytes, provinces: Iterable[str] | None = None) -> list[Stretch]:
    """Every stretch in ``provinces`` (INE codes; None: all), as a straight line.
    A stretch whose province name is unknown is skipped and logged, never guessed.
    One whose two published points do not fit its km range (``plausible`` for a
    straight line: one place, or far apart for a short range) is dropped and
    logged: no road can join them, so the feed would draw a wrong corridor."""
    wanted = frozenset(provinces) if provinces is not None else None
    root = ET.fromstring(xml)
    stretches: list[Stretch] = []
    for loc in root.iter(f"{_D}predefinedLocation"):
        if not loc.get("id"):  # the inner predefinedLocation has no id
            continue
        inner = loc.find(f"{_D}predefinedLocation")
        linear = inner.find(f"{_D}tpeglinearLocation") if inner is not None else None
        start, end = _point(linear.find(f"{_D}from")), _point(linear.find(f"{_D}to"))
        primary = inner.find(f".//{_D}referencePointPrimaryLocation/{_D}referencePoint")
        second = inner.find(f".//{_D}referencePointSecondaryLocation/{_D}referencePoint")
        road = _text(primary, f"{_D}roadNumber")
        area = _text(primary, f"{_D}administrativeArea/{_D}value")
        source_id = loc.get("id", "").removeprefix("GUID_")
        if start is None or end is None or not road:
            log.warning("%s %s: no end points or no road; skipped", KEY, source_id)
            continue
        province = province_code(area)
        if province is None:
            log.warning("%s %s: unknown province %r; skipped", KEY, source_id, area)
            continue
        if wanted is not None and province not in wanted:
            continue
        km_from, km_to = _km(primary), _km(second)
        stretch = Stretch(
            id=f"{KEY}-{source_id}",
            source=KEY,
            name=f"Tramo de radar móvil {_span(road, km_from, km_to)}",
            road=road,
            start=start,
            end=end,
            km_from=km_from,
            km_to=km_to,
            direction=_text(primary, f"{_D}directionRelative"),  # "both"
            province=province,
            url=URL,
            attribution=ATTRIBUTION,
        )
        # Two ends at one place, or far apart for a short km range, are no
        # corridor: the same rule ``follow`` applies to a straight line.
        if not plausible([start, end], stretch, followed=False):
            log.warning(
                "%s %s: %s, points %.0f m apart, do not fit the published km; dropped",
                KEY,
                source_id,
                _span(road, km_from, km_to),
                distance_m(start, end),
            )
            continue
        stretches.append(stretch)
    return stretches


# --- road geometry from OpenStreetMap --------------------------------------


def geometry_query(box: Box, roads: Iterable[str]) -> str | None:
    """Overpass QL for the ways of ``roads`` in one province's box, or None when
    no road ref can be looked up. Exact refs in one alternation, also inside a
    ';' list ('N-332;E-15'); no name regex."""
    roads = set(roads)  # may be a generator: read it once
    refs = sorted({r for r in roads if _REF.match(r)}, key=str.upper)
    for r in sorted(roads - set(refs)):
        log.warning("%s: road ref %r not looked up in OpenStreetMap", KEY, r)
    if not refs:
        return None
    alt = "|".join(r.replace(".", "[.]") for r in refs)
    south, west, north, east = box
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}][bbox:{south},{west},{north},{east}];"
        f'way["highway"]["ref"~"^({alt})(;|$)|;({alt})(;|$)",i];'
        "out geom qt;"
    )


class GeometryError(RuntimeError):
    """Overpass answered, but not with the ways asked for."""


def check_answer(payload: bytes) -> None:
    """Raise unless ``payload`` is a complete Overpass answer with ways in it. A
    query that ran out of time or memory still answers 200, with a ``remark``
    and whatever it had found so far (often nothing)."""
    data = json.loads(payload)
    if data.get("remark"):
        raise GeometryError(f"Overpass remark: {data['remark']}")
    if not any(el.get("type") == "way" for el in data.get("elements", [])):
        raise GeometryError("Overpass answered with no ways")


def overpass(query: str, max_age_s: int) -> bytes:
    """The answer to ``query``, through the file cache. Like ``net.cached_get``,
    but an answer is cached only once ``check_answer`` takes it, and the client
    waits longer than the query's own timeout."""
    digest = hashlib.sha256(query.encode()).hexdigest()[:32]
    path = net.cache_dir() / f"{KEY}-overpass-{digest}.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return path.read_bytes()
    body = net.get(OVERPASS_URL, data={"data": query}, timeout=OVERPASS_WAIT_S, tries=2)
    check_answer(body)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
    except OSError as exc:  # an unwritable cache must not fail a download that worked
        log.warning("could not cache the Overpass answer in %s: %s", path.parent, exc)
    return body


def _refs(tags: dict) -> set[str]:
    return {r.strip().upper() for r in tags.get("ref", "").split(";") if r.strip()}


def road_ways(payload: bytes, road: str) -> list[list[tuple[int, Point]]]:
    """The ways of ``road`` in an Overpass answer, each as (node id, (lat, lon))."""
    out = []
    for el in json.loads(payload).get("elements", []):
        if el.get("type") != "way" or road.upper() not in _refs(el.get("tags", {})):
            continue
        nodes, geom = el.get("nodes", []), el.get("geometry", [])
        if len(nodes) == len(geom) >= 2:
            out.append([(n, (g["lat"], g["lon"])) for n, g in zip(nodes, geom, strict=True)])
    return out


def _length(line: list[Point]) -> float:
    return sum(distance_m(a, b) for a, b in zip(line, line[1:], strict=False))


def route(ways: list[list[tuple[int, Point]]], start: Point, end: Point) -> list[Point] | None:
    """The road from ``start`` to ``end`` along ``ways``, or None.

    The ways form a graph on their shared nodes. A road's ref usually stops at a
    roundabout or a village, so each way's end node is also joined to the nearest
    node of every other way within ``BRIDGE_M``, at twice the cost of a real way.
    Both end points are snapped to the nearest node (None if further than
    ``SNAP_M``: the published point and the mapped road part there), then the
    shortest path is taken. The line runs from the published start to the
    published end."""
    where: dict[int, Point] = {}
    edges: dict[int, list[tuple[float, int]]] = {}

    def link(a: int, b: int, penalty: float = 1.0) -> None:
        d = distance_m(where[a], where[b]) * penalty
        edges.setdefault(a, []).append((d, b))
        edges.setdefault(b, []).append((d, a))

    # Nodes by grid cell, with the ways they belong to, to find bridges fast.
    # Degrees; at Spain's latitudes a cell is wider than BRIDGE_M both ways, so a
    # bridge never spans more than one cell.
    cell = max(BRIDGE_M, 1) / 111_000 * 2
    grid: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, way in enumerate(ways):
        for node, point in way:
            where[node] = point
            grid.setdefault((int(point[0] // cell), int(point[1] // cell)), []).append((node, i))
        for (a, _), (b, _) in zip(way, way[1:], strict=False):
            link(a, b)
    if not where:
        return None
    for i, way in enumerate(ways):
        for end_node, (lat, lon) in (way[0], way[-1]):
            nearest: dict[int, tuple[float, int]] = {}  # other way -> (metres, node)
            gy, gx = int(lat // cell), int(lon // cell)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for node, j in grid.get((gy + dy, gx + dx), ()):
                        if j == i or node == end_node:
                            continue
                        d = distance_m((lat, lon), where[node])
                        if d <= BRIDGE_M and (j not in nearest or d < nearest[j][0]):
                            nearest[j] = (d, node)
            for _, node in nearest.values():
                link(end_node, node, penalty=2.0)  # a real way is preferred to a jump

    def snap(p: Point) -> int | None:
        node = min(where, key=lambda n: distance_m(p, where[n]))
        return node if distance_m(p, where[node]) <= SNAP_M else None

    a, b = snap(start), snap(end)
    if a is None or b is None:
        return None
    dist, back = {a: 0.0}, {}
    queue = [(0.0, a)]
    while queue:
        d, node = heapq.heappop(queue)
        if node == b:
            break
        if d > dist[node]:
            continue
        for step, nxt in edges.get(node, ()):
            if d + step < dist.get(nxt, math.inf):
                dist[nxt], back[nxt] = d + step, node
                heapq.heappush(queue, (d + step, nxt))
    if b not in dist:
        return None
    path = [b]
    while path[-1] != a:
        path.append(back[path[-1]])
    return [start] + [where[n] for n in reversed(path)] + [end]


def plausible(line: list[Point], stretch: Stretch, followed: bool = True) -> bool:
    """Does ``line`` fit the published km range? A path far longer took a wrong
    turn or joins two wrong points; one far shorter snapped both ends to one
    place. A straight line may be far shorter than its road (LP-1 km 0-102 has
    its ends 17 km apart), so for it "far shorter" means the two points are one
    place: under a twentieth of the range, less 100 m."""
    if stretch.km_from is None or stretch.km_to is None:
        return True
    published = abs(stretch.km_to - stretch.km_from) * 1000
    low = 0.5 * published - 1000 if followed else 0.05 * published - 100
    return low <= _length(line) <= 1.5 * published + 2000


def follow(stretches: list[Stretch], payload: bytes | None) -> list[Stretch]:
    """Each stretch with its road as ``line``. One not found (or no ``payload``)
    keeps the straight line and a name that says so; when the straight line does
    not fit the published km range either, the name says that instead."""
    ways: dict[str, list] = {}
    out = []
    for s in stretches:
        line = None
        if payload is not None and s.road:
            if s.road not in ways:
                ways[s.road] = road_ways(payload, s.road)
            line = route(ways[s.road], s.start, s.end)
            if line is not None and not plausible(line, s):
                line = None
        if line is None and not plausible([s.start, s.end], s, followed=False):
            log.warning(
                "%s %s: %s, points %.0f m apart, do not fit the published km; no zones",
                KEY,
                s.id,
                _span(s.road or "?", s.km_from, s.km_to),
                distance_m(s.start, s.end),
            )
            out.append(replace(s, name=s.name + MISMATCH_NOTE))
        elif line is None:
            log.warning("%s %s: road %s not followed; straight line", KEY, s.id, s.road)
            out.append(replace(s, name=s.name + STRAIGHT_NOTE))
        else:
            attribution = f"{s.attribution}; road: {OSM_ATTRIBUTION}"
            out.append(replace(s, line=tuple(line), attribution=attribution))
    return out


def roads_by_province(stretches: list[Stretch], wanted: frozenset[str]) -> dict[str, list]:
    out: dict[str, list[Stretch]] = {}
    for s in stretches:
        if s.province in wanted:
            out.setdefault(s.province, []).append(s)
    return out


def geometry(code: str, stretches: list[Stretch], max_age_s: int) -> bytes | None:
    """The Overpass answer for one province's roads; None when there is no ref
    to look up. A failed or incomplete answer raises: the registry then keeps the
    last good result rather than moving every zone onto a straight line."""
    query = geometry_query(PROVINCES[code][1], (s.road for s in stretches if s.road))
    return overpass(query, max_age_s) if query is not None else None


# --- zones ------------------------------------------------------------------


def along(line: list[Point], every_m: float) -> list[Point]:
    """Points on ``line`` at both ends and evenly between, at most ``every_m`` apart."""
    total = _length(line)
    if total == 0:
        return [line[0]]
    steps = max(1, math.ceil(total / every_m))
    marks = [total * i / steps for i in range(steps + 1)]
    out, walked, k = [], 0.0, 0
    for a, b in zip(line, line[1:], strict=False):
        seg = distance_m(a, b)
        while k < len(marks) and marks[k] <= walked + seg + 1e-6:
            t = (marks[k] - walked) / seg if seg else 0.0
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            k += 1
        walked += seg
    while k < len(marks):  # rounding at the very end
        out.append(line[-1])
        k += 1
    return out


def zones(stretch: Stretch, ctx: Context) -> list[Radar]:
    """Circles along one stretch. Each circle reaches 0.9 of its radius along the
    road both ways, so circles 1.8 radii apart leave no road uncovered, and a
    radar at either end of the stretch sits at a circle's centre.

    A stretch whose road was not followed gets circles only at its two published
    end points, named so: a circle between them could sit off the road. One whose
    end points do not fit the published km range gets none."""
    name = f"Radar móvil {_span(stretch.road or '?', stretch.km_from, stretch.km_to)}"
    if stretch.line is None:
        if not plausible([stretch.start, stretch.end], stretch, followed=False):
            return []
        name += ENDS_NOTE
    template = Radar(
        id="",
        source=KEY,
        kind="mobile_stretch",
        name=name,
        lat=stretch.start[0],
        lon=stretch.start[1],
        radius_m=0,
        url=stretch.url,
        attribution=stretch.attribution,
        direction=stretch.direction,
        province=stretch.province,
    )
    radius = ctx.radius.point(template)
    if stretch.line is None:
        points = along([stretch.start, stretch.end], math.inf)  # the end points, once each
    else:
        points = along(list(stretch.line), 1.8 * radius)
    return [
        replace(template, id=f"{stretch.id}-{i}", lat=lat, lon=lon, radius_m=radius)
        for i, (lat, lon) in enumerate(points)
    ]


def build(xml: bytes, ctx: Context, with_zones: bool, max_age_s: int) -> SourceResult:
    stretches = parse(xml, ctx.provinces)
    if not with_zones:
        return SourceResult(stretches=stretches)
    if ctx.provinces is None:
        log.warning("%s=on needs a province list, not all of Spain; no stretch zones", ZONES_ENV)
        return SourceResult(stretches=stretches)
    groups = roads_by_province(stretches, ctx.provinces)
    followed = {
        s.id: s
        for code, group in sorted(groups.items())
        for s in follow(group, geometry(code, group, max_age_s))
    }
    stretches = [followed.get(s.id, s) for s in stretches]
    radars = [r for s in stretches if s.province in ctx.provinces for r in zones(s, ctx)]
    return SourceResult(radars=radars, stretches=stretches)


def fetch(ctx: Context) -> SourceResult:
    xml = net.cached_get(URL, max_age_s=ctx.max_age_s)
    return build(xml, ctx, ctx.stretch_zones, GEOMETRY_MAX_AGE_S)


SOURCE = Source(
    key=KEY,
    fetch=fetch,
    attribution=ATTRIBUTION,
    licence=LICENCE,
    max_age_s=86_400,
    provinces=COVERED,
)
