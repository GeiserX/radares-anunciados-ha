"""Speed limits and the zone radius they call for.

A real iPhone reports a region entry about 200 m past the edge and some 20 s
later (Apple's own testing figures; the simulator fires at the edge). So a zone
must reach 200 m plus some seconds of travel before the radar, or the alert
lands after the car has passed it:

- fixed and section radars: 200 m + 40 s at the limit (1,533 m at 120 km/h)
- announced streets: 200 m + 20 s at the limit (478 m at 50 km/h); a street is
  covered by many circles, so each one needs less lead

A limit the source doesn't give falls back on the road: 120 km/h on a motorway
(A-, AP-), 90 on any other numbered road, 50 on a street. A limit lookup (OSM
ways around a radar, a road table) can fill ``maxspeed`` first: see
``LOOKUPS``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from . import osm_limits
from .model import REPORTED, Radar

MIN_RADIUS_M = 100  # under 100 m the iOS app splits a zone into 3 of its 20 regions
ENTRY_M = 200  # a phone reports the entry about this far past the edge
POINT_LEAD_S = 40
STREET_LEAD_S = 20
MOTORWAY_KMH, ROAD_KMH, URBAN_KMH = 120, 90, 50

_MOTORWAY = re.compile(r"\b(A|AP)-\d")
_ROAD = re.compile(r"\b[A-Z]{1,3}-\d")

# Functions that fill ``maxspeed`` on radars that lack one, run in order over
# every collected radar before the radii are set. Each takes the whole list and
# returns it with the limits it could find; a radar it can't place keeps
# ``maxspeed=None`` and the road fallback above.
LOOKUPS: list[Callable[[list[Radar]], list[Radar]]] = [osm_limits.fill]


def fill_limits(radars: list[Radar]) -> list[Radar]:
    for lookup in LOOKUPS:
        radars = lookup(radars)
    return radars


def fallback_kmh(radar: Radar) -> int:
    """The limit to size a zone by when nobody published one."""
    if radar.kind in ("mobile_announced", "mobile_recurring"):  # city streets
        return URBAN_KMH
    if _MOTORWAY.search(radar.name):
        return MOTORWAY_KMH
    if _ROAD.search(radar.name):
        return ROAD_KMH
    # A camera with no road and no limit: most mapped ones without a road are on
    # interurban roads, and a zone too large costs a false alert, not a missed one.
    return ROAD_KMH


def auto_radius(limit_kmh: int, lead_s: int) -> int:
    return max(MIN_RADIUS_M, round(ENTRY_M + lead_s * limit_kmh / 3.6))


@dataclass(frozen=True)
class Radius:
    """RADARES_FIXED_RADIUS and RADARES_STREET_RADIUS: metres, or None for auto."""

    fixed: int | None = None
    street: int | None = None

    @classmethod
    def from_env(cls) -> Radius:
        return cls(_setting("RADARES_FIXED_RADIUS"), _setting("RADARES_STREET_RADIUS"))

    def point(self, radar: Radar) -> int:
        if self.fixed is not None:
            return self.fixed
        return auto_radius(radar.maxspeed or fallback_kmh(radar), POINT_LEAD_S)

    def street_m(self, limit_kmh: int | None) -> int:
        """The circle radius along an announced street whose limit is ``limit_kmh``."""
        if self.street is not None:
            return self.street
        return auto_radius(limit_kmh or URBAN_KMH, STREET_LEAD_S)


def _setting(name: str) -> int | None:
    raw = os.environ.get(name, "auto").strip().lower()
    if raw in ("", "auto"):
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is neither a number of metres nor 'auto'") from None


LAID_OUT = ("mobile_announced", "mobile_stretch")  # circles placed along a line


def size(radars: list[Radar], radius: Radius) -> list[Radar]:
    """Set the radius of every point radar (fixed and section ends). Circles along
    a street or a stretch keep theirs: their spacing was laid out for it, and a
    smaller radius would leave road between them uncovered. A report
    (``REPORTED``) gets no zone, so it keeps its radius of 0."""
    return [
        r if r.kind in (*LAID_OUT, REPORTED) else replace(r, radius_m=radius.point(r))
        for r in radars
    ]
