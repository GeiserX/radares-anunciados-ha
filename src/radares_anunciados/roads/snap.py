"""Putting a published point on the road it stands on."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .graph import angle_diff, project, segment_bearing, sides
from .pbf import Network, Point

SNAP_M = 40  # a point further than this from every drivable way is on none
RESNAP_M = 60  # the other carriageway of a road, when the nearest one runs the wrong way
RESNAP_DEG = 45  # how far a carriageway may turn from the wanted travel direction


@dataclass(frozen=True)
class Snap:
    way: int
    seg: int  # the point lies on vertices seg..seg+1 of the way
    point: Point  # the published point moved onto the way
    dist_m: float  # how far it moved


def nearest_on(net: Network, p: Point, ways: Iterable[int], max_m: float) -> Snap | None:
    """The nearest place on ``ways`` within ``max_m`` of ``p``; ties go to the lower way."""
    best = None
    for w in ways:
        pts = net.way_points(w)
        for i in range(len(pts) - 1):
            _, point, d = project(p, pts[i], pts[i + 1])
            if d <= max_m and (best is None or d < best.dist_m):
                best = Snap(w, i, point, d)
    return best


def snap(net: Network, p: Point, refs: frozenset[str] = frozenset(), max_m=SNAP_M) -> Snap | None:
    """The drivable way under ``p``: the nearest within ``max_m``, except that a way
    carrying one of ``refs`` (the road the publisher names) within ``max_m`` wins
    over a nearer one without it."""
    ways = net.ways_near(p, max_m)
    if refs:
        same = [w for w in ways if net.refs[w] & refs]
        found = nearest_on(net, p, same, max_m)
        if found is not None:
            return found
    return nearest_on(net, p, ways, max_m)


def toward(
    net: Network,
    p: Point,
    travel: float,
    refs: frozenset[str],
    max_m: float = RESNAP_M,
    accept: Callable[[int], bool] | None = None,
) -> tuple[Snap, int] | None:
    """The nearest way within ``max_m`` of ``p`` that a car may drive at about
    ``travel`` (within ``RESNAP_DEG``), as (snap, side). With ``refs``, only
    ways carrying one of them."""
    best = None
    for w in net.ways_near(p, max_m):
        if refs and not net.refs[w] & refs:
            continue
        if accept is not None and not accept(w):
            continue
        pts = net.way_points(w)
        for i in range(len(pts) - 1):
            _, point, d = project(p, pts[i], pts[i + 1])
            if d > max_m or (best is not None and d >= best[0].dist_m):
                continue
            for side in sides(net, w):
                if angle_diff(segment_bearing(net, w, i, side), travel) <= RESNAP_DEG:
                    best = (Snap(w, i, point, d), side)
                    break
    return best
