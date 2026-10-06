"""Merge the sources into one list, keep announced streets as dormant zones, and
write it all as GeoJSON."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date, timedelta

from .geo import distance_m
from .model import REPORTED, Radar, Stretch
from .sources import REGISTRY

log = logging.getLogger(__name__)

# A mapped camera (OSM) this close to a radar an authority publishes is the same
# camera mapped twice.
DUPLICATE_M = 150

# A mapped section end this close to an end of a published section is that end.
SECTION_COPY_M = 1000

# Kinds that stand for one camera at one place. A street from a police list
# (mobile_announced), a circle of a DGT mobile-radar stretch (mobile_stretch) and a
# place where fines show a radar on some days only (mobile_recurring) are no
# camera standing there, so a mapped camera near one is no copy of it.
CAMERAS = ("fixed", "section", "trailer")


def _official(r: Radar) -> bool:
    """A camera whose position an authority publishes: its source is marked
    ``official`` in the registry and its kind is in ``CAMERAS``."""
    source = REGISTRY.get(r.source)
    return source is not None and source.official and r.kind in CAMERAS


def _mapped(r: Radar) -> bool:
    """A camera from a source the registry marks as not official (OSM)."""
    return _mapped_source(r.source)


def _mapped_source(key: str) -> bool:
    source = REGISTRY.get(key)
    return source is not None and not source.official


def merge(radars: list[Radar], day: date, missing: frozenset[str] = frozenset()) -> list[Radar]:
    """Radars in force on ``day`` and dormant ones, active first, then by id,
    without duplicates.

    Dropped: mapped cameras that copy an official radar, a place where fines show
    a mobile radar (``mobile_recurring``) within DUPLICATE_M of a camera of another
    source, official or mapped (the camera already warns there, with its own
    limit), and any radar at exactly the spot of one already kept (the DGT lists
    both directions of a section with the same two end points). The phone watches
    only 20 zones; two at one spot waste one. A dormant circle under an active one
    gives way to it.

    A report nobody published (``REPORTED``) is kept as it is and takes no spot:
    it never drops or replaces another radar, and nothing drops it.

    ``missing``: the selected sources with no result at all this run (failed, and
    no last good result). While a camera source among them covers a fines spot's
    province, the spot is held back: the camera that would drop it is unknown.
    """
    wanted = (r for r in radars if not r.active or r.active_on(day))
    ordered = sorted(wanted, key=lambda r: (not r.active, r.id))
    official = [r for r in ordered if _official(r)]
    cameras = [r for r in ordered if r.kind in CAMERAS]
    blind = [REGISTRY[k] for k in sorted(missing) if k in REGISTRY and REGISTRY[k].cameras]
    held = [
        r
        for r in ordered
        if r.kind == "mobile_recurring"
        and any(s.provinces is None or r.province in s.provinces for s in blind)
    ]
    if held:
        log.warning(
            "%d fines spot(s) held back this run: %s gave no cameras yet, and one may stand "
            "beside a spot",
            len(held),
            ", ".join(s.key for s in blind),
        )
    ordered = [r for r in ordered if all(r is not h for h in held)]
    kept: list[Radar] = []
    spots: set[tuple[float, float]] = set()
    for r in ordered:
        if r.kind == REPORTED:
            kept.append(r)
            continue
        spot = (round(r.lat, 5), round(r.lon, 5))
        if spot in spots:
            continue
        if _mapped(r) and any(
            distance_m((r.lat, r.lon), (o.lat, o.lon)) <= DUPLICATE_M for o in official
        ):
            continue
        if r.kind == "mobile_recurring" and any(
            c.source != r.source and distance_m((r.lat, r.lon), (c.lat, c.lon)) <= DUPLICATE_M
            for c in cameras
        ):
            continue
        spots.add(spot)
        kept.append(r)
    return kept


def drop_copied_sections(
    radars: list[Radar], stretches: list[Stretch], day: date
) -> tuple[list[Radar], list[Stretch]]:
    """Mapped sections (OSM) that copy one an authority publishes, gone whole:
    the line and both ends, the radars ``<line id>-from`` and ``-to``.

    A mapped section is a copy when one of its ends is within ``SECTION_COPY_M``
    of an end of a published section, or within ``DUPLICATE_M`` of any published
    camera (``merge`` would drop that end and leave half a section). A section's
    two sources rarely put its ends at the same spot: on 2 Oct 2026 the same
    sections had ends 150 m to 1 km apart, and a driver got an alert from each.
    Only a published radar in force on ``day`` counts, as in ``merge``."""
    official = [r for r in radars if _official(r) and r.active and r.active_on(day)]
    section_ends = [r for r in official if r.kind == "section"]

    def copied(point: tuple[float, float]) -> bool:
        return any(distance_m(point, (o.lat, o.lon)) <= DUPLICATE_M for o in official) or any(
            distance_m(point, (o.lat, o.lon)) <= SECTION_COPY_M for o in section_ends
        )

    gone = {
        s.id for s in stretches if _mapped_source(s.source) and (copied(s.start) or copied(s.end))
    }
    ends = {f"{i}-{which}" for i in gone for which in ("from", "to")}
    return (
        [r for r in radars if not (_mapped(r) and r.id in ends)],
        [s for s in stretches if s.id not in gone],
    )


def remember(
    history: list[Radar], radars: list[Radar], day: date, weeks: int
) -> tuple[list[Radar], list[Radar]]:
    """(remembered radars, the new history) for the streets of periodic lists.

    ``history`` holds the circles of every street a periodic list announced
    (a radar with a ``valid_to``), keyed by source and name. A street in
    ``radars`` today replaces its entry. A street only in the history comes back
    as it was while its period lasts (its source gave nothing this run), and
    dormant once the period has ended: same name, same circles, ``active=False``.
    An entry whose period ended more than ``weeks`` weeks ago is forgotten.
    ``weeks`` <= 0 keeps nothing.
    """
    if weeks <= 0:
        return [], []
    now = [r for r in radars if r.active and r.valid_to is not None and r.active_on(day)]
    announced = {(r.source, r.name) for r in now}
    horizon = day - timedelta(weeks=weeks)
    old = [
        h
        for h in history
        if (h.source, h.name) not in announced and h.valid_to is not None and h.valid_to >= horizon
    ]
    remembered = [h if h.active_on(day) else replace(h, active=False) for h in old]
    return remembered, sorted(old + now, key=lambda r: r.id)


def _common(properties: dict, item: Radar | Stretch) -> dict:
    return properties | {
        "maxspeed": item.maxspeed,
        "direction": item.direction,
        "province": item.province,
        "url": item.url,
        "attribution": item.attribution,
    }


def to_geojson(radars: list[Radar], stretches: list[Stretch] | None = None) -> str:
    features = [
        {
            "type": "Feature",
            "id": r.id,
            "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
            "properties": _common(
                {
                    "name": r.name,
                    "source": r.source,
                    "kind": r.kind,
                    "radius_m": r.radius_m,
                    "active": r.active,
                    "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                    "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                    "reported": r.reported.isoformat() if r.reported else None,
                },
                r,
            ),
        }
        for r in radars
    ]
    for s in stretches or []:
        points = s.line or (s.start, s.end)
        features.append(
            {
                "type": "Feature",
                "id": s.id,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lat, lon in points],
                },
                "properties": _common(
                    {
                        "name": s.name,
                        "source": s.source,
                        "kind": "stretch",
                        "road": s.road,
                        "km_from": s.km_from,
                        "km_to": s.km_to,
                        "active": True,
                    },
                    s,
                ),
            }
        )
    return json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=1
    )
