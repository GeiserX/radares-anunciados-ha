"""``radares roads``: roads.json, the derived bearings in feed.geojson, and the
``roads`` block of status.json. The contract is in docs/roads.md."""

from __future__ import annotations

import csv
import gzip
import json
import logging
import math
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..geo import EARTH_M, bearing_deg, distance_m
from ..sources.dgt_invive import OSM_ATTRIBUTION, route
from . import pbf
from .direction import (
    MILESTONE_SNAP_M,
    Anchor,
    Anchors,
    Decision,
    carriageway,
    decide,
    official,
    osm_tag,
)
from .graph import APPROACH_M, Directed, length_m, point_at, project, segment_bearing, sides
from .pbf import Network, Point, base_class
from .polyline import PRECISION, encode, rounded, simplify
from .snap import RESNAP_M, SNAP_M, Snap, snap, toward

log = logging.getLogger(__name__)

VERSION = 1
FILE = "roads.json"
APPROACH_KINDS = ("fixed", "section", "trailer")
APPROACH_MIN_M = 200  # a shorter approach says nothing a straight line does not
APPROACH_TOLERANCE_M = 5
LINE_TOLERANCE_M = 10
CHORD_MIN_M = 100  # a stretch shorter than this is two points, not a road
RATIO_LOW, RATIO_HIGH = 0.85, 1.15  # road length / published km range
REFLESS_FACTOR, REFLESS_EXTRA_M = 1.6, 500  # a stretch without km: road <= 1.6 x chord + 500 m
REF_MARGIN_M = 5000  # the box searched for the ways of a stretch's road
REFLESS_MARGIN_M = 2000  # the box searched for the ways of a stretch with no road number
KEEP_M = 3000  # ways without a ref are read only this close to a feature
GATE_LEAD_M = 50  # the stretch's first metres give the direction a car enters it
MAX_BYTES = 2_000_000
MAX_GZIP_BYTES = 1_200_000
MIN_FOLLOWED = 0.60  # of the stretches with a road number inside the extract
MIN_APPROACHED = 0.70  # of the point radars inside the extract
CREDIT = f"; carretera: {OSM_ATTRIBUTION}"


class RoadsError(RuntimeError):
    """The roads step refused to publish: too large, or too little followed."""

    def __init__(self, message: str, result: Result | None = None):
        super().__init__(message)
        self.result = result


def dumps(obj) -> str:
    """The compact JSON every published file uses."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _refs(text) -> frozenset[str]:
    if not isinstance(text, str):
        return frozenset()
    return frozenset(r.strip().upper() for r in text.split(";") if r.strip())


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


@dataclass
class PointRow:
    """One radar's audit row."""

    id: str
    source: str
    snapped_m: float | None = None
    s1: str = ""
    anchor: str = ""
    walked_m: float | None = None
    s2: str = ""
    s3: str = ""
    decision: str = ""
    bearing: int | None = None
    confidence: str = ""
    approaches: int = 0

    def csv(self) -> list:
        def m(v):
            return "" if v is None else f"{v:.0f}"

        return [
            self.id,
            self.source,
            m(self.snapped_m),
            self.s1,
            self.anchor,
            m(self.walked_m),
            self.s2,
            self.s3,
            self.decision,
            "" if self.bearing is None else self.bearing,
            self.confidence,
            self.approaches,
        ]


AUDIT_HEADER = [
    "id",
    "source",
    "snapped_m",
    "s1_official_km",
    "s1_anchor",
    "s1_walked_m",
    "s2_oneway",
    "s3_osm_tag",
    "decision",
    "bearing",
    "confidence",
    "approaches",
]


def _signal_text(sig) -> str:
    if sig is None:
        return ""
    side = {1: "forward", -1: "backward", None: "none"}[sig.side]
    return f"{side}: {sig.note}" if sig.note else side


@dataclass
class Result:
    roads: dict
    features: dict  # feature id -> new properties to add
    credited: set[str]
    block: dict
    rows: list[PointRow] = field(default_factory=list)


def _approach(net: Network, local: Directed, at: Snap, side: int, end: Point | None = None):
    """The approach polyline to ``at`` for traffic driving ``side``, or None."""
    ids = net.way_nodes(at.way)
    node = ids[at.seg] if side == 1 else ids[at.seg + 1]
    travel = segment_bearing(net, at.way, at.seg, side)
    walk = local.upstream(at.point, node, at.way, travel, APPROACH_M)
    points = list(reversed(walk))
    if end is not None:
        points[-1] = end
        # the published end may sit beside the road a little behind the last
        # node: no hook back to it
        while len(points) > 2 and _behind(end, points[-2], points[-3]):
            del points[-2]
    if length_m(points) < APPROACH_MIN_M:
        return None
    line = rounded(simplify(points, APPROACH_TOLERANCE_M))
    if len(line) < 2:
        return None
    return {"bearing": travel, "polyline": encode(line)}


def _sorted(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: (e["bearing"], e["polyline"]))


def _direction_for(net: Network, at: Snap, p: Point, travel: float, refs: frozenset[str]):
    """``at`` if a car may drive its way at ``travel``, else the nearest way of the
    same road within 60 m that runs that way: (snap, side) or None."""
    for side in sides(net, at.way):
        if abs((segment_bearing(net, at.way, at.seg, side) - travel + 180) % 360 - 180) <= 90:
            return at, side
    return toward(net, p, travel, refs or net.refs[at.way], RESNAP_M)


def _gate(net: Network, line: list[Point], refs: frozenset[str], at_start: bool):
    """The approaches that drive into a stretch at one end."""
    ordered = line if at_start else list(reversed(line))
    end = ordered[0]
    lead = point_at(ordered, min(GATE_LEAD_M, length_m(ordered) / 2))
    if distance_m(end, lead) == 0:
        return []
    inward = bearing_deg(end, lead)
    # The way that runs into the stretch here, in line with it; at a junction the
    # nearest way can be a crossing road.
    found = toward(net, end, inward, refs, SNAP_M)
    if found is None:
        at = snap(net, end, refs)
        found = _direction_for(net, at, end, inward, refs) if at is not None else None
    if found is None:
        return []
    where, side = found
    local = Directed(net, net.ways_near(end, APPROACH_M + 300))
    entry = _approach(net, local, where, side, end=end)
    return [entry] if entry else []


def _route(net: Network, start: Point, end: Point, refs: frozenset[str], km: float | None):
    """The road between a stretch's two ends, or None when it is not found or does
    not fit what the publisher says (the sidecar band, stricter than the feed's)."""
    chord = distance_m(start, end)
    south, north = min(start[0], end[0]), max(start[0], end[0])
    west, east = min(start[1], end[1]), max(start[1], end[1])

    def box(margin_m):
        dlat = margin_m / 111_000
        dlon = margin_m / (111_000 * math.cos(math.radians((south + north) / 2)))
        return south - dlat, west - dlon, north + dlat, east + dlon

    cls = None
    if km is None:
        ends = [snap(net, start, refs), snap(net, end, refs)]
        if None in ends:
            return None
        classes = {base_class(net.highway[s.way]) for s in ends}
        if len(classes) != 1:
            return None
        cls = classes.pop()
    if refs:
        ways = net.ref_ways_in_box(refs, *box(REF_MARGIN_M))
    else:
        ways = net.ways_in_box(*box(REFLESS_MARGIN_M))
    if cls is not None:
        ways = [w for w in ways if base_class(net.highway[w]) == cls]
    if not ways:
        return None
    graph = [list(zip(net.way_nodes(w), net.way_points(w), strict=True)) for w in ways]
    line = route(graph, start, end)
    if line is None:
        return None
    line = trim_ends(line)
    road = length_m(line)
    if km is not None:
        if km <= 0 or not RATIO_LOW <= road / (km * 1000) <= RATIO_HIGH:
            return None
    elif road > REFLESS_FACTOR * chord + REFLESS_EXTRA_M:
        return None
    return line


def trim_ends(line: list[Point]) -> list[Point]:
    """``line`` without the back-and-forth at its ends. ``route`` joins each
    published end to the nearest node of the road, which can lie behind it: on a
    motorway with nodes 500 m apart the line would start by driving 200 m
    backwards and turn round. A node the end already lies past is dropped."""
    pts = list(line)
    while len(pts) > 2 and _behind(pts[0], pts[1], pts[2]):
        del pts[1]
    while len(pts) > 2 and _behind(pts[-1], pts[-2], pts[-3]):
        del pts[-2]
    return pts


def _behind(end: Point, node: Point, nxt: Point) -> bool:
    """Does ``end`` lie past ``node`` on the way to ``nxt``, with ``node`` on the
    straight run from ``end`` to ``nxt``? The second test keeps a sharp bend: at a
    hairpin the end projects past the bend's node too, but that node is far off the
    end-to-next chord and must stay."""
    t, _, beside = project(end, node, nxt)
    if t <= 0:
        return False
    return _off_line(node, end, nxt) <= max(LINE_TOLERANCE_M, min(beside, SNAP_M))


def _off_line(p: Point, a: Point, b: Point) -> float:
    """Metres from ``p`` to the straight line through ``a`` and ``b``."""
    kx = math.cos(math.radians(p[0])) * math.pi / 180 * EARTH_M
    ky = math.pi / 180 * EARTH_M
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    seg = math.hypot(bx - ax, by - ay)
    if seg == 0:
        return math.hypot(ax, ay)
    return abs(ax * by - ay * bx) / seg


def keep_cells(features: list[dict]) -> set[tuple[int, int]]:
    """The grid cells near any feature, where ways with no road number matter."""
    cells: set[tuple[int, int]] = set()
    for f in features:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") == "Point":
            cells.update(pbf.cells_around(coords[1], coords[0], KEEP_M))
        elif geom.get("type") == "LineString" and coords:
            lats = [c[1] for c in coords]
            lons = [c[0] for c in coords]
            dlat = KEEP_M / 111_000
            dlon = KEEP_M / (111_000 * math.cos(math.radians(lats[0])))
            cells.update(
                pbf.cells_in_box(
                    min(lats) - dlat, min(lons) - dlon, max(lats) + dlat, max(lons) + dlon
                )
            )
    return cells


def anchors_of(features: list[dict], net: Network) -> list[Anchor]:
    out = []
    for f in features:
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") == "Point":
            refs, km = _refs(p.get("road_ref") or p.get("road")), _num(p.get("km"))
            if refs and km is not None:
                out.append(Anchor(str(f.get("id")), (coords[1], coords[0]), km, refs))
        elif geom.get("type") == "LineString" and len(coords) >= 2:
            refs = _refs(p.get("road"))
            if not refs:
                continue
            for which, km, c in (
                ("start", _num(p.get("km_from")), coords[0]),
                ("end", _num(p.get("km_to")), coords[-1]),
            ):
                if km is not None:
                    out.append(Anchor(f"{f.get('id')}:{which}", (c[1], c[0]), km, refs))
    for node, lat, lon, km in net.milestones:
        out.append(Anchor(f"osm-node-{node}", (lat, lon), km, None, MILESTONE_SNAP_M))
    return out


def _endpoints(features: list[dict]) -> set[tuple[float, float]]:
    ends = set()
    for f in features:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") == "LineString" and len(coords) >= 2:
            ends.add(tuple(coords[0]))
            ends.add(tuple(coords[-1]))
    return ends


def compute(feed: dict, net: Network, osm_date: str | None, now: float) -> Result:
    features = feed.get("features") or []
    ends = _endpoints(features)
    anchors = Anchors(net, anchors_of(features, net))
    roads: dict[str, dict] = {}
    added: dict[str, dict] = {}
    credited: set[str] = set()
    rows: list[PointRow] = []
    counts = dict.fromkeys(
        (
            "stretches_in_bbox",
            "stretches_followed",
            "stretches_with_gates",
            "points_in_bbox",
            "points_with_approaches",
            "points_with_bearing",
            "points_high_confidence",
        ),
        0,
    )
    ref_in_bbox = ref_followed = 0
    for f in features:
        geom = f.get("geometry") or {}
        p = f.get("properties") or {}
        fid = f.get("id")
        coords = geom.get("coordinates") or []
        if not isinstance(fid, str):
            continue
        if geom.get("type") == "LineString" and len(coords) >= 2:
            start, end = (coords[0][1], coords[0][0]), (coords[-1][1], coords[-1][0])
            inside = net.in_bbox(start) and net.in_bbox(end)
            refs = _refs(p.get("road"))
            counts["stretches_in_bbox"] += inside
            ref_in_bbox += inside and bool(refs)
            if distance_m(start, end) < CHORD_MIN_M:
                continue
            km_from, km_to = _num(p.get("km_from")), _num(p.get("km_to"))
            km = abs(km_to - km_from) if km_from is not None and km_to is not None else None
            line = _route(net, start, end, refs, km)
            if line is None:
                continue
            gates = {"start": _gate(net, line, refs, True), "end": _gate(net, line, refs, False)}
            simple = simplify(line, LINE_TOLERANCE_M)
            entry: dict = {"line": encode(simple)}
            if gates["start"] or gates["end"]:
                entry["gates"] = gates
                counts["stretches_with_gates"] += inside
            roads[fid] = entry
            credited.add(fid)
            counts["stretches_followed"] += inside
            ref_followed += inside and bool(refs)
            continue
        if geom.get("type") != "Point" or p.get("kind") not in APPROACH_KINDS:
            continue
        if p.get("kind") == "section" and tuple(coords) in ends:
            continue  # folded into its stretch by the app, which never looks it up
        point = (coords[1], coords[0])
        inside = net.in_bbox(point)
        counts["points_in_bbox"] += inside
        row = PointRow(fid, str(p.get("source") or ""))
        rows.append(row)
        refs = _refs(p.get("road_ref") or p.get("road"))
        at = snap(net, point, refs)
        if at is None:
            row.decision = "no drivable way within 40 m"
            continue
        row.snapped_m = at.dist_m
        allowed = sides(net, at.way)
        both = p.get("direction") == "both" or p.get("km_direction") == "both"
        s1 = official(net, at, _num(p.get("km")), p.get("km_direction"), refs, anchors, own_id=fid)
        s2 = carriageway(net, at, point)
        direction = p.get("direction")
        node = int(fid[4:]) if fid.startswith("osm-") and fid[4:].isdigit() else None
        s3 = osm_tag(net, at, direction, node) if isinstance(direction, str) and not both else None
        row.s1, row.s2, row.s3 = _signal_text(s1), _signal_text(s2), _signal_text(s3)
        if s1 is not None:
            row.anchor, row.walked_m = s1.anchor or "", s1.walked_m
        decision = Decision(None, reason="both directions") if both else decide(s1, s2, s3, allowed)
        row.decision = decision.reason or decision.source or ""
        targets: list[tuple[Snap, int]] = []
        if decision.side is not None:
            travel = segment_bearing(net, at.way, at.seg, decision.side)
            found = (
                toward(net, point, travel, refs or net.refs[at.way], RESNAP_M)
                if decision.resnap
                else (at, decision.side)
            )
            if found is None:
                # The official direction names the other carriageway, beyond a wide
                # median. An approach on the one at hand would point the app at the
                # traffic the radar does not watch: publish nothing, today's rule runs.
                row.decision = "no carriageway runs the official direction within 60 m"
                continue
            targets.append(found)
            where, side = found
            bearing = segment_bearing(net, where.way, where.seg, side)
            added[fid] = {
                "bearing": bearing,
                "direction_semantics": "travel",
                "direction_confidence": decision.confidence,
                "bearing_source": decision.source,
            }
            credited.add(fid)
            row.bearing, row.confidence = bearing, decision.confidence or ""
            counts["points_with_bearing"] += inside
            counts["points_high_confidence"] += inside and decision.confidence == "high"
        if decision.side is None:
            if both:
                for side in (1, -1):
                    travel = segment_bearing(net, at.way, at.seg, side)
                    found = _direction_for(net, at, point, travel, refs)
                    if found is not None and found not in targets:
                        targets.append(found)
            else:
                targets = [(at, side) for side in allowed]
        local = Directed(net, net.ways_near(point, APPROACH_M + 300))
        entries = [e for e in (_approach(net, local, s, side) for s, side in targets) if e]
        row.approaches = len(entries)
        if entries:
            roads[fid] = {"approaches": _sorted(entries)}
            credited.add(fid)
            counts["points_with_approaches"] += inside
    doc = {"attribution": OSM_ATTRIBUTION, "features": roads, "precision": PRECISION}
    doc["version"] = VERSION
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    raw = len(text.encode("utf-8"))
    zipped = len(gzip.compress(text.encode("utf-8"), mtime=0))
    block = {
        "file": FILE,
        "status": "ok",
        "generated": datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds"),
        "osm_date": osm_date,
        "bbox": list(net.bbox) if net.bbox else None,
        **counts,
        "bytes": raw,
        "error": "",
    }
    problems = []
    if raw > MAX_BYTES:
        problems.append(f"roads.json is {raw:,} bytes, over {MAX_BYTES:,}")
    if zipped > MAX_GZIP_BYTES:
        problems.append(f"roads.json is {zipped:,} bytes gzipped, over {MAX_GZIP_BYTES:,}")
    if ref_in_bbox and ref_followed / ref_in_bbox < MIN_FOLLOWED:
        problems.append(
            f"only {ref_followed} of {ref_in_bbox} stretches with a road number were followed"
        )
    if counts["points_in_bbox"] and (
        counts["points_with_approaches"] / counts["points_in_bbox"] < MIN_APPROACHED
    ):
        problems.append(
            f"only {counts['points_with_approaches']} of {counts['points_in_bbox']} radars"
            " got an approach"
        )
    result = Result({"text": text, "gzip_bytes": zipped}, added, credited, block, rows)
    if problems:
        raise RoadsError("; ".join(problems), result)
    return result


def enrich(feed: dict, added: dict[str, dict], credited: set[str]) -> dict:
    """``feed`` with the derived properties and the OSM credit. Geometry and every
    existing property stay as they are; new keys go at the end."""
    for f in feed.get("features") or []:
        fid = f.get("id")
        props = f.get("properties")
        if not isinstance(props, dict):
            continue
        props.update(added.get(fid, {}))
        if fid in credited:
            attribution = props.get("attribution") or ""
            if OSM_ATTRIBUTION not in attribution:
                props["attribution"] = attribution + CREDIT
    return feed


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(path)


def _status(path: Path, block: dict) -> None:
    report = json.loads(path.read_text("utf-8")) if path.exists() else {}
    report["roads"] = block
    _write(path, json.dumps(report, ensure_ascii=False, indent=1))


def write_audit(path: Path, rows: list[PointRow]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(AUDIT_HEADER)
        for row in rows:
            out.writerow(row.csv())


def run(
    feed_path: Path,
    status_path: Path,
    pbf_path: Path,
    output: Path,
    audit: Path | None = None,
    fallback: Path | None = None,
    now: float | None = None,
    net: Network | None = None,
) -> dict:
    """The whole step. On any failure the feed is left as it was, the previous
    roads.json from ``fallback`` is put back, status.json says ``stale`` with the
    error, and the error is raised again."""
    import time

    now = time.time() if now is None else now
    try:
        feed = json.loads(feed_path.read_text("utf-8"))
        if net is None:
            net = pbf.load(pbf_path, keep_cells(feed.get("features") or []))
        try:
            result = compute(feed, net, net.osm_date, now)
        except RoadsError as exc:
            if audit is not None and exc.result is not None:
                write_audit(audit, exc.result.rows)
            raise
        if audit is not None:
            write_audit(audit, result.rows)
        _write(output, result.roads["text"])
        _write(feed_path, dumps(enrich(feed, result.features, result.credited)))
        _status(status_path, result.block)
        if fallback is not None:
            fallback.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output, fallback / FILE)
            _write(fallback / "status.json", json.dumps(result.block, ensure_ascii=False))
        log.info(
            "roads: %s, %d bytes raw, %d gzipped",
            {k: v for k, v in result.block.items() if k.startswith(("stretches", "points"))},
            result.block["bytes"],
            result.roads["gzip_bytes"],
        )
        return result.block
    except Exception as exc:
        block = {"file": FILE, "status": "stale"}
        previous = fallback / FILE if fallback is not None else None
        if previous is not None and previous.exists():
            shutil.copyfile(previous, output)
            saved = fallback / "status.json"
            if saved.exists():
                block = json.loads(saved.read_text("utf-8")) | block
        else:
            block |= {"bytes": 0}
        block["error"] = f"{type(exc).__name__}: {exc}"[:300]
        try:
            _status(status_path, block)
        except Exception:
            log.exception("could not write the roads block of %s", status_path)
        raise
