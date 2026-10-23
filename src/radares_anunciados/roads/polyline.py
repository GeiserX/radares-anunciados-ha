"""Google encoded polylines (precision 5) and Douglas-Peucker simplification.

Encoding: https://developers.google.com/maps/documentation/utilities/polylinealgorithm,
latitude then longitude per vertex, each rounded to 1e-5 degrees (about 1.1 m).
"""

from __future__ import annotations

import math

from ..geo import EARTH_M

Point = tuple[float, float]  # (lat, lon)
PRECISION = 5
_SCALE = 10**PRECISION


def _round(value: float) -> int:
    # Half away from zero, as the reference implementation's Math.round on |value|.
    scaled = abs(value) * _SCALE
    return int(math.floor(scaled + 0.5)) * (1 if value >= 0 else -1)


def _chunk(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    out = []
    while value >= 0x20:
        out.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    out.append(chr(value + 63))
    return "".join(out)


def encode(points: list[Point]) -> str:
    """The polyline5 text of ``points``."""
    out, last_lat, last_lon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = _round(lat), _round(lon)
        out.append(_chunk(ilat - last_lat))
        out.append(_chunk(ilon - last_lon))
        last_lat, last_lon = ilat, ilon
    return "".join(out)


def decode(text: str) -> list[Point]:
    """The vertices of a polyline5 text."""
    points, index, lat, lon = [], 0, 0, 0
    while index < len(text):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= len(text):
                    raise ValueError("truncated polyline")
                byte = ord(text[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / _SCALE, lon / _SCALE))
    return points


def rounded(points: list[Point]) -> list[Point]:
    """``points`` as they decode after encoding, with repeated vertices dropped."""
    out: list[Point] = []
    for p in decode(encode(points)):
        if not out or out[-1] != p:
            out.append(p)
    return out


def simplify(points: list[Point], tolerance_m: float) -> list[Point]:
    """Douglas-Peucker: the fewest vertices of ``points`` that keep every dropped
    vertex within ``tolerance_m`` of the result. The two ends always stay."""
    if len(points) < 3:
        return list(points)
    lat0 = math.radians(sum(p[0] for p in points) / len(points))
    kx = math.cos(lat0) * math.pi / 180 * EARTH_M
    ky = math.pi / 180 * EARTH_M
    xy = [(p[1] * kx, p[0] * ky) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        ax, ay = xy[first]
        bx, by = xy[last]
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        worst, at = -1.0, -1
        for i in range(first + 1, last):
            px, py = xy[i]
            if seg == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > worst:
                worst, at = d, i
        if worst > tolerance_m:
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))
    return [p for p, k in zip(points, keep, strict=True) if k]
