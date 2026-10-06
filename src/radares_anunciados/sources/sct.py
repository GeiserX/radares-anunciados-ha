"""Fixed, section and trailer radars of the Servei Català de Trànsit (SCT).

Catalonia runs its own traffic enforcement, so the DGT file has none of its
radars. The SCT publishes two text files, linked from
https://transit.gencat.cat/ca/seguretat_viaria/cinemometres-fixos-trams-mobils/:

- ``radars.txt``: fixed radars and the cameras of its section radars (a row
  whose PK is a range, "501,5-506,5"). A title line "(ETRS89)", a blank line,
  then fixed-width columns ``Via PK Velocitat X Y``.
- ``radars-remolc.txt``: the published spots where a trailer radar can stand.
  Tab-separated ``Via PK Velocitat X Y``, CRLF.

Both use a decimal comma and ETRS89 UTM zone 31N (EPSG:25831), and both give
the speed limit. Neither gives a direction or a province: the province comes
from the point (``catalonia_shapes``). "C-32 nord" and "C-32 sud" are the two
separately numbered halves of the C-32, so the Via column is kept whole.

Some rows carry coordinates that are not a place in Catalonia (a missing
decimal comma, two northings in one row). Such a row is skipped and logged,
never repaired by guessing where the comma went.

Left out: the page's table of stretches where mobile radars work (road and a
km range, no coordinates). Placing a km from OpenStreetMap's km markers was
measured against this file's own radars on 2026-10-01: median 34 m, but 27 of
250 points more than 300 m off and the worst 20 km, because one road number can
carry two km sequences (the C-58 motorway and the old C-58 by Montserrat). No
check at run time can tell which placements are wrong, so none are drawn.

A file that loses more than ``MAX_SKIPPED`` of its rows is refused as a whole:
on 2026-10-01 17 of 247 rows were broken, and an export that breaks a third of
them would otherwise replace the last good result and delete valid zones.

Reuse: the files fall under the gencat.cat reuse terms, the "Llicència oberta
d'ús d'informació – Catalunya" (the open-data catalogue lists radars.txt as
dataset re3y-fftf with that licence). It asks to cite the source as
"Generalitat de Catalunya. Departament de ... . [organisme]" and to state the
date of the last update: the ``Last-Modified`` the file came with, kept beside
the cached copy (``net.cached_get_dated``) and added to the attribution.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import replace

from .. import net
from ..geo import utm_to_wgs84
from ..model import Radar, SourceResult, dated
from .base import Context, Source
from .catalonia_shapes import province

log = logging.getLogger(__name__)

FIXED_URL = "https://transit.gencat.cat/web/.content/documents/seguretat_viaria/radars.txt"
TRAILER_URL = "https://transit.gencat.cat/web/.content/documents/seguretat_viaria/radars-remolc.txt"
ATTRIBUTION = (
    "Generalitat de Catalunya. Departament d'Interior i Seguretat Pública. "
    "Servei Català de Trànsit (Llicència oberta d'ús d'informació – Catalunya)"
)
LICENCE = "Llicència oberta d'ús d'informació – Catalunya"
PROVINCES = frozenset({"08", "17", "25", "43"})
UTM_ZONE = 31
MAX_SKIPPED = 1 / 3  # share of unusable rows past which the file is refused

# ETRS89 UTM 31N of Catalonia with a margin. Outside it a row's coordinates are
# broken, and some broken ones would overflow the projection.
_EASTING = (240_000.0, 540_000.0)
_NORTHING = (4_480_000.0, 4_760_000.0)
_NUMBER = r"\d+(?:,\d+)?"
_PK = re.compile(rf"^({_NUMBER})(?:\s*-\s*({_NUMBER}))?$")
_SPEED = re.compile(r"^\d{2,3}$")


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _km(text: str) -> str:
    """'445,35' as '445.35'; the published figure, not a rounded one."""
    return text.replace(",", ".")


HEADER = ["Via", "PK", "Velocitat", "X", "Y"]


def fixed_rows(text: str) -> Iterator[list[str]]:
    """[via, pk, speed, x, y] of each row of ``radars.txt``. The columns are cut
    where the header puts them, so a road name with a space ("C-32 nord") or a
    range PK ("1,8 -3,0") stays in one piece."""
    lines = text.splitlines()
    header = next((i for i, line in enumerate(lines) if line.split()[:1] == ["Via"]), None)
    if header is None or lines[header].split() != HEADER:
        raise ValueError(f"radars.txt has no {' '.join(HEADER)!r} header")
    starts = [lines[header].index(name) for name in HEADER]
    bounds = list(zip(starts, [*starts[1:], None], strict=True))
    for line in lines[header + 1 :]:
        if line.strip():
            yield [line[a:b].strip() for a, b in bounds]


def trailer_rows(text: str) -> Iterator[list[str]]:
    """[via, pk, speed, x, y] of each row of ``radars-remolc.txt``."""
    lines = text.splitlines()
    if not lines or [c.strip() for c in lines[0].split("\t")] != HEADER:
        raise ValueError(f"radars-remolc.txt has no {' '.join(HEADER)!r} header")
    for line in lines[1:]:
        if line.strip():
            yield [c.strip() for c in line.split("\t")]


def _radar(row: list[str], key: str, url: str, trailer: bool) -> Radar | None:
    """The radar of one row, or None (logged) when the row is not usable."""
    if len(row) != 5:
        log.warning("%s: row %r has %d columns, not 5; skipped", key, row, len(row))
        return None
    road, pk, speed, x, y = row
    pk_match = _PK.match(pk)
    try:
        easting, northing = _number(x), _number(y)
    except ValueError:
        easting = northing = float("nan")
    if not road or not pk_match or not _SPEED.match(speed):
        log.warning("%s: row %r is not 'road PK limit X Y'; skipped", key, row)
        return None
    in_box = _EASTING[0] <= easting <= _EASTING[1] and _NORTHING[0] <= northing <= _NORTHING[1]
    if in_box:
        lat, lon = utm_to_wgs84(easting, northing, UTM_ZONE)
        code = province(lat, lon)
    if not in_box or code is None:
        log.warning("%s: %s PK %s at X %s Y %s is not in Catalonia; skipped", key, road, pk, x, y)
        return None
    start, end = pk_match.groups()
    km = _km(start) + (f"-{_km(end)}" if end else "")
    if trailer:
        kind, label = "trailer", "Radar en remolque"
    elif end:
        kind, label = "section", "Radar de tramo"
    else:
        kind, label = "fixed", "Radar fijo"
    return Radar(
        id=f"{key}-{road.replace(' ', '')}-{km}",
        source=key,
        kind=kind,
        name=f"{label} {road} km {km}",
        lat=lat,
        lon=lon,
        radius_m=500,  # sized later from the limit (speed.size)
        url=url,
        attribution=ATTRIBUTION,
        maxspeed=int(speed),
        province=code,
    )


def _parse(rows: Iterator[list[str]], key: str, url: str, trailer: bool) -> list[Radar]:
    """Every usable row. Two rows at one PK (a radar in each direction, two
    trailer spots) get "-2", "-3" on their ids, in file order. A file that
    yields no radar at all, or loses more than ``MAX_SKIPPED`` of its rows,
    raises: its format changed or the export broke, and the last good result is
    better than a gutted one."""
    radars: list[Radar] = []
    seen: dict[str, int] = {}
    total = 0
    for row in rows:
        total += 1
        radar = _radar(row, key, url, trailer)
        if radar is None:
            continue
        seen[radar.id] = seen.get(radar.id, 0) + 1
        if seen[radar.id] > 1:
            radar = replace(radar, id=f"{radar.id}-{seen[radar.id]}")
        radars.append(radar)
    if not radars:
        raise ValueError(f"{key}: no usable row in {url}")
    skipped = total - len(radars)
    if skipped > MAX_SKIPPED * total:
        raise ValueError(f"{key}: {skipped} of {total} rows unusable in {url}; file refused")
    if skipped:
        log.warning("%s: %d of %d rows skipped", key, skipped, total)
    return radars


def parse_fixed(text: str) -> list[Radar]:
    return _parse(fixed_rows(text), "sct", FIXED_URL, trailer=False)


def parse_trailer(text: str) -> list[Radar]:
    return _parse(trailer_rows(text), "sct_remolc", TRAILER_URL, trailer=True)


def _fetch(ctx: Context, url: str, parse: Callable[[str], list[Radar]]) -> SourceResult:
    body, modified = net.cached_get_dated(url, max_age_s=ctx.max_age_s)
    radars = parse(body.decode("utf-8", errors="replace"))
    updated = net.last_modified_day(modified)
    if updated is None:
        log.warning("%s came with no Last-Modified; credited without a date", url)
    if ctx.provinces is not None:
        radars = [r for r in radars if r.province in ctx.provinces]
    return dated(SourceResult(radars=radars), updated)


def fetch_fixed(ctx: Context) -> SourceResult:
    return _fetch(ctx, FIXED_URL, parse_fixed)


def fetch_trailer(ctx: Context) -> SourceResult:
    return _fetch(ctx, TRAILER_URL, parse_trailer)


SOURCE = Source(
    key="sct",
    cameras=True,
    fetch=fetch_fixed,
    attribution=ATTRIBUTION,
    licence=LICENCE,
    max_age_s=86_400,
    provinces=PROVINCES,
)

TRAILER = Source(
    key="sct_remolc",
    cameras=True,
    fetch=fetch_trailer,
    attribution=ATTRIBUTION,
    licence=LICENCE,
    max_age_s=86_400,
    provinces=PROVINCES,
)
