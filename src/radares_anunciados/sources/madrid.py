"""Madrid city's fixed and section radars, from its open data portal.

Dataset 300049 "Radares" on https://datos.madrid.es (CC BY 4.0). The portal's
general reuse conditions ask for the source to be cited as "Origen de los datos:
Ayuntamiento de Madrid" and for the date of the last update. The CSV is found
through the CKAN API, so a renamed resource is still found.

The CSV is UTF-8 with a BOM, ``;``-separated, WGS84. A fixed radar is one row;
a section ("Radar de tramo") row holds its start in "Longitud/Latitud inicio
tramo" and its exit camera in "Longitud/Latitud". A row that does not fit the
header (a shifted or split column, an unknown type, a point outside Madrid) is
skipped and logged, never guessed. No mobile radars are published, despite the
dataset's name.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import unicodedata

from .. import net
from ..geo import distance_m
from ..model import Radar, SourceResult, Stretch
from ..provinces import PROVINCES
from .base import Context, Source

log = logging.getLogger(__name__)

PROVINCE = "28"
DATASET = "300049-0-radares-fijos-moviles"
API = f"https://datos.madrid.es/api/3/action/package_show?id={DATASET}"
ATTRIBUTION = "Origen de los datos: Ayuntamiento de Madrid (CC BY 4.0)"

# The columns read, by their header with accents and case folded. The file's
# own spelling ("Carretara") is kept; "Velocidad limite" comes from "límite".
_NUMBER = "no radar"
_PLACE = "ubicacion"
_DIRECTION = "sentido"
_TYPE = "tipo"
_START_LON = "longitud inicio tramo"
_START_LAT = "latitud inicio tramo"
_LON = "longitud"
_LAT = "latitud"
_LIMIT = "velocidad limite"
_NEEDED = (_NUMBER, _PLACE, _DIRECTION, _TYPE, _START_LON, _START_LAT, _LON, _LAT, _LIMIT)

_FIXED = "fijo"
# Madrid lists one row per lane and per section end, so one gantry can be two
# or three rows a few metres apart (the furthest pair in the 2026 file is 34 m).
# The phone watches only 20 zones: a camera within this distance of an earlier
# row with the same limit is the same place and gives no zone of its own.
SAME_SITE_M = 50
_SECTION = "radar de tramo"


def _fold(text: str) -> str:
    """'Nº RADAR' -> 'no radar', 'Velocidad límite' -> 'velocidad limite'."""
    text = unicodedata.normalize("NFKD", text.replace("º", "o"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def _clean(text: str) -> str:
    return " ".join(text.split())


def csv_resource(package: bytes) -> tuple[str, str | None]:
    """(CSV download URL, last update day) from a CKAN package_show answer."""
    data = json.loads(package)
    if not data.get("success"):
        raise ValueError(f"CKAN package_show for {DATASET} did not succeed")
    result = data["result"]
    urls = [
        r["url"] for r in result.get("resources", []) if str(r.get("format", "")).upper() == "CSV"
    ]
    if len(urls) != 1:
        raise ValueError(f"expected one CSV resource in {DATASET}, found {len(urls)}")
    updated = result.get("modified") or result.get("metadata_modified")
    return urls[0], (str(updated)[:10] if updated else None)


def _coord(row: dict[str, str], lat_key: str, lon_key: str) -> tuple[float, float] | None:
    """(lat, lon) from two cells, None when either is blank. Raises on a cell
    that is not a number."""
    lat, lon = row[lat_key].strip(), row[lon_key].strip()
    if not lat or not lon:
        return None
    return float(lat.replace(",", ".")), float(lon.replace(",", "."))


def _in_madrid(point: tuple[float, float]) -> bool:
    south, west, north, east = PROVINCES[PROVINCE][1]
    return south <= point[0] <= north and west <= point[1] <= east


def parse(payload: bytes, url: str, updated: str | None = None) -> SourceResult:
    """Every radar of the CSV that can be read. Raises when the header lacks a
    column it needs or no row can be read, so a changed file never empties the
    feed (the registry keeps the last good result instead)."""
    text = payload.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    if not rows:
        raise ValueError("empty CSV")
    header = [_fold(h) for h in rows[0]]
    missing = [c for c in _NEEDED if c not in header]
    if missing:
        raise ValueError(f"CSV header lacks {missing}: {rows[0]}")
    attribution = ATTRIBUTION + (f", actualizado {updated}" if updated else "")

    radars: list[Radar] = []
    stretches: list[Stretch] = []
    seen: set[str] = set()
    for line, cells in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(header):
            log.warning(
                "madrid: row %d has %d columns, not %d; skipped: %s",
                line,
                len(cells),
                len(header),
                cells,
            )
            continue
        row = dict(zip(header, cells, strict=True))
        number = row[_NUMBER].strip()
        kind = _fold(row[_TYPE])
        try:
            if not number.isdigit():
                raise ValueError(f"radar number {number!r} is not a number")
            if number in seen:
                raise ValueError(f"radar number {number} appears twice")
            if kind not in (_FIXED, _SECTION):
                raise ValueError(f"unknown type {row[_TYPE]!r}")
            end = _coord(row, _LAT, _LON)
            if end is None:
                raise ValueError("no position")
            start = _coord(row, _START_LAT, _START_LON) if kind == _SECTION else None
            for point in (end, start):
                if point is not None and not _in_madrid(point):
                    raise ValueError(f"position {point} is outside Madrid")
            limit = row[_LIMIT].strip()
            maxspeed = int(limit) if limit.isdigit() else None
        except ValueError as exc:
            log.warning("madrid: row %d skipped (%s): %s", line, exc, cells)
            continue
        seen.add(number)
        place = _clean(row[_PLACE])
        direction = _clean(row[_DIRECTION]) or None
        common = dict(
            source="madrid",
            radius_m=500,
            url=url,
            attribution=attribution,
            maxspeed=maxspeed,
            direction=direction,
            province=PROVINCE,
        )
        if kind == _FIXED:
            radars.append(
                Radar(
                    id=f"madrid-{number}",
                    kind="fixed",
                    name=f"Radar fijo {place}",
                    lat=end[0],
                    lon=end[1],
                    **common,
                )
            )
            continue
        # A section has a camera at each end. Without a published start, only
        # the exit camera is known: no stretch is drawn.
        ends = {"to": end} if start is None else {"from": start, "to": end}
        for which, (lat, lon) in ends.items():
            radars.append(
                Radar(
                    id=f"madrid-{number}-{which}",
                    kind="section",
                    name=f"Radar de tramo {place}",
                    lat=lat,
                    lon=lon,
                    **common,
                )
            )
        if start is not None:
            stretches.append(
                Stretch(
                    id=f"madrid-{number}",
                    source="madrid",
                    name=f"Tramo {place}",
                    road=_clean(row.get("carretara o vial", "")) or None,
                    start=start,
                    end=end,
                    maxspeed=maxspeed,
                    direction=direction,
                    province=PROVINCE,
                    url=url,
                    attribution=attribution,
                )
            )
    if not radars:
        raise ValueError("no radar could be read from the CSV")
    return SourceResult(radars=_one_per_site(radars), stretches=stretches, updated=updated)


def _one_per_site(radars: list[Radar]) -> list[Radar]:
    """The radars in file order, without a camera that sits within SAME_SITE_M
    of an earlier one with the same limit (the lowest Nº keeps its id)."""
    kept: list[Radar] = []
    for r in radars:
        twin = next(
            (
                k
                for k in kept
                if k.maxspeed == r.maxspeed
                and distance_m((k.lat, k.lon), (r.lat, r.lon)) <= SAME_SITE_M
            ),
            None,
        )
        if twin is None:
            kept.append(r)
        else:
            log.info("madrid: %s is at the site of %s; one zone kept", r.id, twin.id)
    return kept


def fetch(ctx: Context) -> SourceResult:
    url, updated = csv_resource(net.cached_get(API, max_age_s=ctx.max_age_s))
    return parse(net.cached_get(url, max_age_s=ctx.max_age_s), url, updated)


SOURCE = Source(
    key="madrid",
    cameras=True,
    fetch=fetch,
    attribution=ATTRIBUTION,
    licence="CC BY 4.0",
    max_age_s=86_400,
    provinces=frozenset({PROVINCE}),
)
