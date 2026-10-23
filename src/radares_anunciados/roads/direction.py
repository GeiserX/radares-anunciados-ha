"""Which way the traffic a radar watches drives, from three independent signals.

Each signal names a side of the way the radar snapped to: 1 along the way's
node order, -1 against it.

- S1 ``official_km``: the publisher says the radar watches traffic going up
  ("positive") or down ("negative") the km markers. Walking the radar's road
  both ways finds km anchors (other radars with a km, stretch ends, OSM
  milestones); the side where the km grows is the positive one. An anchor
  counts only when the road distance walked to it matches its km difference,
  which throws out anchors from a second km sequence on the same road number.
- S2 ``oneway``: the radar stands on a one-way carriageway and the other
  carriageway of the same road runs the opposite way within 60 m, at least
  5 m further from the radar.
- S3 ``osm_tag``: the OpenStreetMap ``direction`` (a bearing, or forward /
  backward) points along one of the way's travel directions, within 30 degrees.

``decide`` publishes a bearing only when no two signals oppose, and calls it
high confidence only when S1 and S2 are both present and agree: two independent
sources, one official, one the shape of the road.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..geo import distance_m
from .graph import angle_diff, project, reach, segment_bearing, undirected
from .pbf import Network, Point
from .snap import Snap, nearest_on

CARRIAGEWAY_M = 60  # the other carriageway of a dual road is closer than this
CARRIAGEWAY_CLEAR_M = 5  # the radar must be this much nearer its own carriageway
AGREE_DEG = 30  # a bearing this close to a travel direction names it
WALK_M = 25_000  # how far along its road a radar looks for km anchors
ANCHOR_MIN_KM = 0.5  # a closer km marker says too little about the direction
ANCHOR_SLACK = 0.15  # walked distance may differ from the km difference by 15 % ...
ANCHOR_SLACK_M = 300  # ... plus 300 m
ANCHOR_SNAP_M = 40  # a feed point or stretch end used as an anchor, on its road
MILESTONE_SNAP_M = 30  # an OSM milestone, on a way of the radar's road

OFFICIAL, ONEWAY, OSM_TAG = "official_km", "oneway", "osm_tag"
STRENGTH = (OFFICIAL, ONEWAY, OSM_TAG)


@dataclass(frozen=True)
class Anchor:
    id: str
    point: Point
    km: float
    refs: frozenset[str] | None  # None: an OSM milestone, on whatever road it stands
    snap_m: float = ANCHOR_SNAP_M


@dataclass(frozen=True)
class Signal:
    side: int | None
    note: str = ""
    anchor: str | None = None
    walked_m: float | None = None


def anchor_valid(walked_m: float, delta_km: float) -> bool:
    """Does an anchor ``delta_km`` of markers away, reached after ``walked_m`` of
    road, belong to the radar's own km sequence?"""
    delta = abs(delta_km) * 1000
    if abs(delta_km) < ANCHOR_MIN_KM:
        return False
    return abs(walked_m - delta) <= ANCHOR_SLACK * delta + ANCHOR_SLACK_M


class Anchors:
    """Every km anchor, with its place on the road worked out once."""

    def __init__(self, net: Network, anchors: list[Anchor]):
        self.net = net
        self.anchors = anchors
        self._snaps: dict[tuple[str, str], Snap | None] = {}

    def snapped(self, anchor: Anchor, ref: str) -> Snap | None:
        key = (anchor.id, ref)
        if key not in self._snaps:
            ways = [
                w
                for w in self.net.ways_near(anchor.point, anchor.snap_m)
                if ref in self.net.refs[w]
            ]
            self._snaps[key] = nearest_on(self.net, anchor.point, ways, anchor.snap_m)
        return self._snaps[key]

    def near(self, p: Point, refs: frozenset[str], radius_m: float):
        for a in self.anchors:
            if a.refs is not None and not (a.refs & refs):
                continue
            if abs(a.point[0] - p[0]) * 111_000 > radius_m:
                continue
            if distance_m(a.point, p) <= radius_m:
                yield a


def official(
    net: Network,
    snap: Snap,
    km: float | None,
    km_direction: str | None,
    refs: frozenset[str],
    anchors: Anchors,
    own_id: str = "",
) -> Signal | None:
    """S1: the side of ``snap`` whose travel the publisher's km direction names."""
    if km_direction not in ("positive", "negative") or km is None or not refs:
        return None
    road = refs & net.refs[snap.way]
    if not road:
        return Signal(None, "the nearest way does not carry the road")
    lat, lon = snap.point
    dlat = WALK_M / 111_000
    dlon = WALK_M / 80_000  # Spain lies above 27 N: a degree of longitude is under 99 km
    ways = net.ref_ways_in_box(road, lat - dlat, lon - dlon, lat + dlat, lon + dlon)
    _, edges = undirected(net, ways)
    pts, ids = net.way_points(snap.way), net.way_nodes(snap.way)
    a, b = ids[snap.seg], ids[snap.seg + 1]
    da, db = distance_m(snap.point, pts[snap.seg]), distance_m(snap.point, pts[snap.seg + 1])
    sources = {a: (da, da, -1)}
    if b != a and (b not in sources or db < sources[b][0]):
        sources[b] = (db, db, 1)
    reached = reach(edges, sources, WALK_M)
    found = []
    for anchor in anchors.near(snap.point, road, WALK_M):
        if anchor.id == own_id or abs(anchor.km - km) < ANCHOR_MIN_KM:
            continue
        for ref in sorted(road if anchor.refs is None else road & anchor.refs):
            at = anchors.snapped(anchor, ref)
            if at is None:
                continue
            a_pts, a_ids = net.way_points(at.way), net.way_nodes(at.way)
            best = None
            for k in (at.seg, at.seg + 1):
                hit = reached.get(a_ids[k])
                if hit is not None:
                    walked = hit[0] + distance_m(a_pts[k], at.point)
                    if best is None or walked < best[0]:
                        best = (walked, hit[1])
            if best is None:
                continue
            walked, side = best
            if anchor_valid(walked, anchor.km - km):
                grows = side if anchor.km > km else -side
                found.append((walked, anchor.id, grows))
            break
    if not found:
        return Signal(None, "no km anchor on the road")
    found.sort()
    if len({g for _, _, g in found}) > 1:
        return Signal(None, "km anchors disagree", found[0][1], found[0][0])
    walked, anchor_id, grows = found[0]
    side = grows if km_direction == "positive" else -grows
    return Signal(side, "", anchor_id, walked)


def carriageway(net: Network, snap: Snap, p: Point) -> Signal | None:
    """S2: the radar's one-way carriageway, when the other one of the same road
    runs the opposite way nearby and the radar is clearly nearer its own."""
    own = net.oneway[snap.way]
    road = net.refs[snap.way]
    if not own or not road:
        return None
    travel = segment_bearing(net, snap.way, snap.seg, own)
    other = None
    for w in net.ways_near(p, CARRIAGEWAY_M):
        if w == snap.way or not net.oneway[w] or not net.refs[w] & road:
            continue
        pts = net.way_points(w)
        for i in range(len(pts) - 1):
            _, _, d = project(p, pts[i], pts[i + 1])
            if d > CARRIAGEWAY_M or (other is not None and d >= other):
                continue
            b = segment_bearing(net, w, i, net.oneway[w])
            if angle_diff(b, travel) >= 180 - AGREE_DEG:
                other = d
    if other is None:
        return None
    if other - snap.dist_m < CARRIAGEWAY_CLEAR_M:
        return Signal(None, f"between the carriageways ({snap.dist_m:.0f} m / {other:.0f} m)")
    return Signal(own, f"{snap.dist_m:.0f} m from its carriageway, {other:.0f} m from the other")


def osm_tag(
    net: Network, snap: Snap, direction: str | None, node: int | None = None
) -> Signal | None:
    """S3: an OpenStreetMap ``direction`` along the snapped way. ``forward`` and
    ``backward`` name the node order of the way the camera node ``node`` belongs
    to, so they count only when that node is on the snapped way and every drivable
    way through it runs the same way there."""
    if not direction:
        return None
    text = direction.strip().lower()
    if text in ("forward", "backward"):
        side = 1 if text == "forward" else -1
        if node is None or node not in net.way_nodes(snap.way):
            return Signal(None, f"{text}: the camera node is not on the snapped way")
        if not _one_node_order(net, snap, node):
            return Signal(None, f"{text}: the ways through the camera node disagree")
        return Signal(side, text)
    try:
        value = float(text)
    except ValueError:
        return None
    along = segment_bearing(net, snap.way, snap.seg, 1)
    if angle_diff(value, along) <= AGREE_DEG:
        return Signal(1, f"{value:g}")
    if angle_diff(value, along + 180) <= AGREE_DEG:
        return Signal(-1, f"{value:g}")
    return Signal(None, f"{value:g} along neither way of the road")


def _one_node_order(net: Network, snap: Snap, node: int) -> bool:
    """Do all drivable ways through ``node`` run the same way at it (node order
    within 90 degrees)? A road split in two ways drawn in opposite orders does not."""
    bearings = []
    for w in net.ways_near(snap.point, 50):
        ids = net.way_nodes(w)
        if node not in ids:
            continue
        k = ids.index(node)
        if len(ids) < 2:
            continue
        seg = min(k, len(ids) - 2)
        bearings.append(segment_bearing(net, w, seg, 1))
    return all(angle_diff(a, b) <= 90 for a in bearings for b in bearings)


@dataclass(frozen=True)
class Decision:
    side: int | None  # None: no bearing
    source: str | None = None
    confidence: str | None = None
    resnap: bool = False  # the bearing's carriageway is not the nearest way
    reason: str = ""


def decide(
    s1: Signal | None, s2: Signal | None, s3: Signal | None, allowed: tuple[int, ...]
) -> Decision:
    """The published direction, from the signals that gave a side.

    Any two that oppose publish nothing, with one exception: when S1 (official)
    and S2 (the nearest carriageway) oppose, the radar watches the other
    carriageway; S1 wins and the confidence is low. A bearing from the OSM tag
    that a one-way way does not allow is a conflict too."""
    sig = {
        name: s.side
        for name, s in ((OFFICIAL, s1), (ONEWAY, s2), (OSM_TAG, s3))
        if s is not None and s.side is not None
    }
    overridden = OFFICIAL in sig and ONEWAY in sig and sig[OFFICIAL] != sig[ONEWAY]
    if overridden:
        del sig[ONEWAY]
    if not sig:
        return Decision(None, reason="no signal")
    if len(set(sig.values())) > 1:
        return Decision(None, reason="conflict: " + ", ".join(f"{k}={v}" for k, v in sig.items()))
    source = next(name for name in STRENGTH if name in sig)
    side = sig[source]
    resnap = side not in allowed
    if resnap and source != OFFICIAL:
        return Decision(None, reason=f"conflict: {source}={side} against the one-way way")
    high = (
        not overridden
        and s1 is not None
        and s2 is not None
        and s1.side is not None
        and s1.side == s2.side
    )
    return Decision(
        side,
        source,
        "high" if high else "low",
        resnap,
        "official km against the nearest carriageway" if overridden else "",
    )
