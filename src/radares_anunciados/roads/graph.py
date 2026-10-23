"""Small graphs built around one radar or stretch: geometry helpers, the upstream
walk that draws an approach, and the walk along one road that finds km anchors."""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable

from ..geo import EARTH_M, bearing_deg, distance_m
from .pbf import Network, Point

APPROACH_M = 1500  # how much road leads up to a radar
TURN_MAX_DEG = 100  # a sharper turn is never taken by an approach
BRIDGE_M = 500  # as dgt_invive.route: joins two ways of one road across a gap


def angle_diff(a: float, b: float) -> float:
    """The smaller angle between two compass bearings, 0 to 180."""
    return abs((a - b + 180) % 360 - 180)


def project(p: Point, a: Point, b: Point) -> tuple[float, Point, float]:
    """(t along a->b in 0..1, the nearest point of the segment to ``p``, metres)."""
    kx = math.cos(math.radians(p[0])) * math.pi / 180 * EARTH_M
    ky = math.pi / 180 * EARTH_M
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    seg = dx * dx + dy * dy
    t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
    x, y = ax + t * dx, ay + t * dy
    point = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return t, point, math.hypot(x, y)


def length_m(points: list[Point]) -> float:
    return sum(distance_m(a, b) for a, b in zip(points, points[1:], strict=False))


def point_at(points: list[Point], metres: float) -> Point:
    """The point ``metres`` along ``points`` (the last one past the end)."""
    walked = 0.0
    for a, b in zip(points, points[1:], strict=False):
        seg = distance_m(a, b)
        if seg > 0 and walked + seg >= metres:
            t = (metres - walked) / seg
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        walked += seg
    return points[-1]


def sides(net: Network, w: int) -> tuple[int, ...]:
    """The directions a car may drive way ``w``: 1 along its node order, -1 against."""
    o = net.oneway[w]
    return (o,) if o else (1, -1)


def segment_bearing(net: Network, w: int, seg: int, side: int) -> int:
    pts = net.way_points(w)
    a, b = pts[seg], pts[seg + 1]
    return bearing_deg(a, b) if side == 1 else bearing_deg(b, a)


class Directed:
    """The ways of ``ways`` as a directed graph: for each node, the edges that
    arrive at it, as (previous node, way)."""

    def __init__(self, net: Network, ways: Iterable[int]):
        self.net = net
        self.where: dict[int, Point] = {}
        self.inc: dict[int, list[tuple[int, int]]] = {}
        for w in ways:
            ids, pts = net.way_nodes(w), net.way_points(w)
            o = net.oneway[w]
            for k in range(len(ids) - 1):
                a, b = ids[k], ids[k + 1]
                if a == b:
                    continue
                self.where.setdefault(a, pts[k])
                self.where.setdefault(b, pts[k + 1])
                if o >= 0:
                    self.inc.setdefault(b, []).append((a, w))
                if o <= 0:
                    self.inc.setdefault(a, []).append((b, w))

    def upstream(
        self, start: Point, node: int, way: int, travel: float, metres: float = APPROACH_M
    ) -> list[Point]:
        """The road a car drives to reach ``start``, walked backwards from it: the
        first step goes to ``node`` (the upstream end of the segment ``start`` lies
        on, along ``way``; ``travel`` is the bearing a car drives there). At a node
        where several edges arrive, the same road ref wins, then the same highway
        class, then the smallest turn; a turn over
        ``TURN_MAX_DEG`` is never taken. Stops after ``metres``, at a dead end or
        at a node already walked. Points from ``start`` upstream."""
        net = self.net
        out = [start]
        walked = 0.0
        ahead = start  # the point the car drives to after ``node``
        visited = {node}
        current = way
        leaving = travel
        while True:
            here = self.where.get(node)
            if here is None:
                break
            step = distance_m(ahead, here)
            if walked + step >= metres:
                t = (metres - walked) / step if step else 0.0
                lat = ahead[0] + t * (here[0] - ahead[0])
                out.append((lat, ahead[1] + t * (here[1] - ahead[1])))
                break
            if step > 0:
                out.append(here)
                walked += step
            if step > 0.5:
                leaving = bearing_deg(here, ahead)
            best = None
            for prev, w in self.inc.get(node, ()):
                before = self.where[prev]
                if distance_m(before, here) == 0:
                    continue
                turn = angle_diff(bearing_deg(before, here), leaving)
                if turn > TURN_MAX_DEG:
                    continue
                rank = (
                    0 if net.refs[w] and net.refs[w] & net.refs[current] else 1,
                    0 if net.highway[w] // 2 == net.highway[current] // 2 else 1,
                    turn,
                    prev,
                )
                if best is None or rank < best[0]:
                    best = (rank, prev, w)
            if best is None:
                break
            _, prev, w = best
            if prev in visited:
                break
            visited.add(prev)
            ahead, node, current = here, prev, w
        return out


def undirected(net: Network, ways: list[int]):
    """The ways as an undirected graph, each way's end joined to the nearest node
    of every other way within ``BRIDGE_M`` at twice the cost, as
    ``dgt_invive.route`` does. Returns (where, edges); an edge is
    (cost, metres, node)."""
    where: dict[int, Point] = {}
    edges: dict[int, list[tuple[float, float, int]]] = {}

    def link(a: int, b: int, penalty: float = 1.0) -> None:
        d = distance_m(where[a], where[b])
        edges.setdefault(a, []).append((d * penalty, d, b))
        edges.setdefault(b, []).append((d * penalty, d, a))

    size = 2 * BRIDGE_M / 111_000
    grid: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for w in ways:
        ids, pts = net.way_nodes(w), net.way_points(w)
        for node, p in zip(ids, pts, strict=True):
            where[node] = p
            grid.setdefault((int(p[0] // size), int(p[1] // size)), []).append((node, w))
        for a, b in zip(ids, ids[1:], strict=False):
            if a != b:
                link(a, b)
    for w in ways:
        ids, pts = net.way_nodes(w), net.way_points(w)
        for end, (lat, lon) in ((ids[0], pts[0]), (ids[-1], pts[-1])):
            nearest: dict[int, tuple[float, int]] = {}
            gy, gx = int(lat // size), int(lon // size)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for node, other in grid.get((gy + dy, gx + dx), ()):
                        if other == w or node == end:
                            continue
                        d = distance_m((lat, lon), where[node])
                        if d <= BRIDGE_M and (other not in nearest or d < nearest[other][0]):
                            nearest[other] = (d, node)
            for _, node in nearest.values():
                link(end, node, penalty=2.0)
    return where, edges


def reach(
    edges: dict[int, list[tuple[float, float, int]]],
    sources: dict[int, tuple[float, float, int]],
    limit_m: float,
) -> dict[int, tuple[float, int]]:
    """Dijkstra from several labelled sources: node -> (metres walked, label of
    the source it was reached from), up to ``limit_m`` of cost. ``sources`` maps a
    node to (cost, metres, label)."""
    best: dict[int, float] = {}
    out: dict[int, tuple[float, int]] = {}
    queue = []
    for node, (cost, metres, label) in sources.items():
        heapq.heappush(queue, (cost, metres, label, node))
    while queue:
        cost, metres, label, node = heapq.heappop(queue)
        if node in out:
            continue
        out[node] = (metres, label)
        for step_cost, step_m, nxt in edges.get(node, ()):
            c = cost + step_cost
            if c <= limit_m and nxt not in out and c < best.get(nxt, math.inf):
                best[nxt] = c
                heapq.heappush(queue, (c, metres + step_m, label, nxt))
    return out
