"""The records every source produces."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

# The time zone the published lists mean. A daily or weekly list starts at
# Spanish midnight, whatever time zone the container runs in (UTC by default).
SPAIN = ZoneInfo("Europe/Madrid")

# The kind of a place people report and no source publishes (an open OpenStreetMap
# note). It goes in the feed and on the map, never in a zone (``ha.zoned``), and
# never drops or replaces another radar (``feed.merge``).
REPORTED = "reported"


def today_in_spain(now: float | None = None) -> date:
    """Spain's date (peninsular time) at ``now`` (epoch seconds; None: the clock)."""
    return datetime.fromtimestamp(time.time() if now is None else now, SPAIN).date()


@dataclass(frozen=True)
class Radar:
    """A place where a speed control is published, drawn as one circle.

    A fixed radar is one circle. A street from a weekly police list is several
    circles along the street, each its own Radar with the same ``name``.
    """

    id: str  # stable across runs: source + source id (+ circle index)
    source: str  # "dgt", "osm", "murcia"
    # "fixed", "section", "trailer", "mobile_announced", "mobile_stretch", REPORTED, and
    # "mobile_recurring": a place where traffic fines show a radar stood on some days
    kind: str
    name: str  # what the driver reads in the alert
    lat: float
    lon: float
    radius_m: int
    valid_from: date | None = None  # None = always
    valid_to: date | None = None
    url: str | None = None  # where the position was published
    attribution: str = ""
    maxspeed: int | None = None  # km/h, when the source or the map says it
    direction: str | None = None  # as the source writes it: "ALMERIA", "200", "forward"
    province: str | None = None  # INE province code, "30"; None when the source doesn't say
    # False: a street from a periodic list whose period ended. It keeps its zone,
    # with another icon, until it is announced again or ages out.
    active: bool = True
    reported: date | None = None  # a REPORTED one: the day a person reported it

    def active_on(self, day: date) -> bool:
        if self.valid_from and day < self.valid_from:
            return False
        if self.valid_to and day > self.valid_to:
            return False
        return True


@dataclass(frozen=True)
class Stretch:
    """A road stretch where a control is published, such as a DGT section or a
    stretch where mobile radars run. Written to the feed as a line; it never
    becomes a zone by default (a 40 km stretch is no place for a circle)."""

    id: str
    source: str
    name: str
    road: str | None
    start: tuple[float, float]  # (lat, lon)
    end: tuple[float, float]
    line: tuple[tuple[float, float], ...] | None = None  # (lat, lon) points, start to end
    km_from: float | None = None
    km_to: float | None = None
    maxspeed: int | None = None
    direction: str | None = None
    province: str | None = None
    url: str | None = None
    attribution: str = ""


@dataclass(frozen=True)
class Announced:
    """One line of a police list: a street and, usually, its district."""

    street: str  # "Camino de Tiñosa"
    place: str | None  # "Los Dolores"

    def label(self) -> str:
        return self.street + (f" ({self.place})" if self.place else "")


@dataclass
class WeeklyList:
    """What one periodic police list gave this run: the metrics and the
    notification report it, because a skipped street is a radar with no warning."""

    source: str  # "murcia"
    week: date  # the Monday
    published: date | None = None  # None: no list found for this week yet
    streets: list[Announced] = field(default_factory=list)
    skipped: list[Announced] = field(default_factory=list)  # not placed on the map


@dataclass
class SourceResult:
    """Everything one source gave in one fetch."""

    radars: list[Radar] = field(default_factory=list)
    stretches: list[Stretch] = field(default_factory=list)
    lists: list[WeeklyList] = field(default_factory=list)
    # The day the publisher says the data last changed ("2025-12-18"), when it says
    # one. Reuse terms ask for it (LICENSE-DATA.md); never guessed.
    updated: str | None = None


def dated(result: SourceResult, day: str | None) -> SourceResult:
    """``result`` credited with the day its data last changed: in each record's
    attribution and in ``updated``. None changes nothing."""
    if day is None:
        return result
    return replace(
        result,
        radars=[
            replace(r, attribution=f"{r.attribution}, actualizado {day}") for r in result.radars
        ],
        stretches=[
            replace(s, attribution=f"{s.attribution}, actualizado {day}") for s in result.stretches
        ],
        updated=day,
    )
