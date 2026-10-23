"""The drivable roads of an OpenStreetMap extract, held compactly.

One pass over the file with pyosmium keeps the ways tagged with a drivable
``highway`` class and the ``highway=milestone`` nodes with a km value. Way
coordinates and node ids go in flat arrays with per-way offsets; tags are
reduced to the road refs, the class and the one-way rule. A grid of 0.02 degree
cells lists the ways whose bounding box touches each cell, so a caller builds a
small graph around one radar instead of a graph of all Spain.

pyosmium is imported inside ``load``: ``radares feed``, and the Home Assistant
image that never installs the ``roads`` group, never import it.
"""

from __future__ import annotations

import logging
import math
from array import array
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

Point = tuple[float, float]  # (lat, lon)

# The classes a car may drive on, in order; a "_link" shares its road's class.
HIGHWAYS = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
)
_CODE = {name: i * 2 for i, name in enumerate(HIGHWAYS)} | {
    f"{name}_link": i * 2 + 1 for i, name in enumerate(HIGHWAYS)
}
CELL_DEG = 0.02
_NO_REFS: frozenset[str] = frozenset()


def base_class(code: int) -> int:
    """The class of a highway code with "_link" folded into its road's class."""
    return code // 2


def class_name(code: int) -> str:
    return HIGHWAYS[code // 2] + ("_link" if code % 2 else "")


def refs_of(tags) -> frozenset[str]:
    """The road numbers of a way, upper-cased: ``ref=A-7;E-15`` -> {A-7, E-15}."""
    raw = tags.get("ref") or ""
    found = frozenset(r.strip().upper() for r in raw.split(";") if r.strip())
    return found or _NO_REFS


def way_attrs(tags) -> tuple[int, frozenset[str], int] | None:
    """(highway code, refs, one-way) of a drivable way, or None. One-way is 1
    along the node order, -1 against it, 0 both ways; a motorway, a motorway
    link and a roundabout are one-way unless tagged ``oneway=no``."""
    code = _CODE.get(tags.get("highway") or "")
    if code is None:
        return None
    oneway = (tags.get("oneway") or "").strip().lower()
    if oneway in ("yes", "true", "1"):
        direction = 1
    elif oneway in ("-1", "reverse"):
        direction = -1
    elif oneway in ("no", "false", "0"):
        direction = 0
    else:
        implied = tags.get("junction") in ("roundabout", "circular") or code in (0, 1)
        direction = 1 if implied else 0
    return code, refs_of(tags), direction


def milestone_km(tags) -> float | None:
    """The km of a ``highway=milestone`` node, from ``distance`` (or ``pk``)."""
    if tags.get("highway") != "milestone":
        return None
    for key in ("distance", "pk"):
        raw = (tags.get(key) or "").strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value) and value >= 0:
            return value
    return None


def cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG))


def cells_around(lat: float, lon: float, radius_m: float) -> Iterable[tuple[int, int]]:
    dlat = radius_m / 111_000
    dlon = radius_m / (111_000 * max(0.2, math.cos(math.radians(lat))))
    return cells_in_box(lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def cells_in_box(south: float, west: float, north: float, east: float):
    y0, x0 = cell(south, west)
    y1, x1 = cell(north, east)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            yield (y, x)


class Network:
    """Drivable ways: vertices of way ``w`` are ``start[w]`` to ``start[w + 1] - 1``."""

    def __init__(self, bbox: tuple[float, float, float, float] | None = None):
        self.coords = array("d")  # lat, lon, lat, lon...
        self.nodes = array("q")  # OSM node id per vertex
        self.start = array("q", [0])
        self.refs: list[frozenset[str]] = []
        self.highway = array("b")
        self.oneway = array("b")
        self.boxes = array("d")  # south, west, north, east per way
        self.grid: dict[tuple[int, int], array] = {}
        self.milestones: list[tuple[int, float, float, float]] = []  # (node, lat, lon, km)
        self.bbox = bbox  # (min lon, min lat, max lon, max lat) of the extract, when it says
        self.osm_date: str | None = None
        self._by_ref: dict[str, list[int]] | None = None
        self._interned: dict[frozenset[str], frozenset[str]] = {}

    def __len__(self) -> int:
        return len(self.refs)

    def add_way(
        self, nodes: list[int], points: list[Point], code: int, refs: frozenset[str], oneway: int
    ) -> int:
        w = len(self.refs)
        for node, (lat, lon) in zip(nodes, points, strict=True):
            self.nodes.append(node)
            self.coords.append(lat)
            self.coords.append(lon)
        self.start.append(len(self.nodes))
        self.refs.append(self._interned.setdefault(refs, refs))
        self.highway.append(code)
        self.oneway.append(oneway)
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        box = (min(lats), min(lons), max(lats), max(lons))
        self.boxes.extend(box)
        for c in cells_in_box(*box):
            bucket = self.grid.get(c)
            if bucket is None:
                bucket = self.grid[c] = array("i")
            bucket.append(w)
        self._by_ref = None
        return w

    def way_points(self, w: int) -> list[Point]:
        a, b = self.start[w], self.start[w + 1]
        c = self.coords
        return [(c[2 * i], c[2 * i + 1]) for i in range(a, b)]

    def way_nodes(self, w: int) -> list[int]:
        return list(self.nodes[self.start[w] : self.start[w + 1]])

    def box(self, w: int) -> tuple[float, float, float, float]:
        return tuple(self.boxes[4 * w : 4 * w + 4])

    def ways_near(self, p: Point, radius_m: float) -> list[int]:
        """Ways whose bounding box comes within ``radius_m`` of ``p`` (sorted)."""
        dlat = radius_m / 111_000
        dlon = radius_m / (111_000 * max(0.2, math.cos(math.radians(p[0]))))
        return self.ways_in_box(p[0] - dlat, p[1] - dlon, p[0] + dlat, p[1] + dlon)

    def ways_in_box(self, south: float, west: float, north: float, east: float) -> list[int]:
        found: set[int] = set()
        for c in cells_in_box(south, west, north, east):
            found.update(self.grid.get(c, ()))
        out = []
        for w in found:
            s, wst, n, e = self.boxes[4 * w : 4 * w + 4]
            if s <= north and n >= south and wst <= east and e >= west:
                out.append(w)
        out.sort()
        return out

    def ways_of_ref(self, ref: str) -> list[int]:
        if self._by_ref is None:
            by_ref: dict[str, list[int]] = {}
            for w, refs in enumerate(self.refs):
                for r in refs:
                    by_ref.setdefault(r, []).append(w)
            self._by_ref = by_ref
        return self._by_ref.get(ref.upper(), [])

    def ref_ways_in_box(self, refs: Iterable[str], south, west, north, east) -> list[int]:
        out = set()
        for ref in refs:
            for w in self.ways_of_ref(ref):
                s, wst, n, e = self.boxes[4 * w : 4 * w + 4]
                if s <= north and n >= south and wst <= east and e >= west:
                    out.add(w)
        return sorted(out)

    def in_bbox(self, p: Point) -> bool:
        """Is ``p`` inside the extract's header box (True when the file gives none)?"""
        if self.bbox is None:
            return True
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return min_lat <= p[0] <= max_lat and min_lon <= p[1] <= max_lon


def header(path: str | Path) -> tuple[tuple[float, float, float, float] | None, str | None]:
    """The header box (min lon, min lat, max lon, max lat) and the day of the data."""
    import osmium

    reader = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
    try:
        head = reader.header()
        box = head.box()
        bbox = None
        if box.valid():
            bbox = (
                box.bottom_left.lon,
                box.bottom_left.lat,
                box.top_right.lon,
                box.top_right.lat,
            )
        stamp = head.get("osmosis_replication_timestamp", "") or head.get("timestamp", "")
    finally:
        reader.close()
    day = stamp[:10] if len(stamp) >= 10 and stamp[4] == "-" else None
    if day is None:
        day = datetime.fromtimestamp(Path(path).stat().st_mtime, UTC).date().isoformat()
    return bbox, day


def load(path: str | Path, keep_cells: set[tuple[int, int]] | None = None) -> Network:
    """The drivable ways and milestones of the extract at ``path``.

    With ``keep_cells``, a way without a road ref is kept only when its bounding
    box touches one of those cells; a way with a ref is always kept, because the
    km anchors of a radar can lie 25 km along its road."""
    import osmium

    bbox, day = header(path)
    net = Network(bbox)
    net.osm_date = day
    wanted = [("highway", name) for name in _CODE] + [("highway", "milestone")]
    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.TagFilter(*wanted))
    )
    broken = 0
    for obj in processor:
        if obj.is_node():
            km = milestone_km(obj.tags)
            if km is not None and obj.location.valid():
                net.milestones.append((obj.id, obj.location.lat, obj.location.lon, km))
            continue
        if not obj.is_way():
            continue
        attrs = way_attrs(obj.tags)
        if attrs is None:
            continue
        ids, points = [], []
        try:
            for n in obj.nodes:
                ids.append(n.ref)
                points.append((n.lat, n.lon))
        except osmium.InvalidLocationError:
            broken += 1
            continue
        if len(points) < 2:
            continue
        code, refs, oneway = attrs
        if keep_cells is not None and not refs:
            lats = [p[0] for p in points]
            lons = [p[1] for p in points]
            box = (min(lats), min(lons), max(lats), max(lons))
            if not any(c in keep_cells for c in cells_in_box(*box)):
                continue
        net.add_way(ids, points, code, refs, oneway)
    if broken:
        log.warning("%d ways with a node missing from the extract; skipped", broken)
    log.info(
        "roads: %d ways, %d vertices, %d milestones from %s (data of %s)",
        len(net),
        len(net.nodes),
        len(net.milestones),
        path,
        net.osm_date,
    )
    return net
