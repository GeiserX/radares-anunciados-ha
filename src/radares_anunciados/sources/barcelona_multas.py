"""Where Barcelona's mobile radars stood, from the city's traffic-fines open data.

Dataset "denuncies_sancions_transit_bcn_detall" on Open Data BCN (CC BY 4.0):
every fine the Institut Municipal d'Hisenda processed, one CSV per quarter, each
with the date, the time and the WGS84 point of the place it names. The portal's
conditions ask for "Fuente de los datos: Ayuntamiento de Barcelona", the date of
the last update, and for any transformation to be stated: ours groups the fines
by place and day.

The direct CSV download sits behind a captcha; the portal's datastore SQL API
answers without one, so one query groups the newest quarter's speed fines from
cameras (``MITJA_IMPOSICIO`` "MTO", "medio técnico operativo (imágenes)") by
place and day, with the first and last time of each day.

A fixed camera fines day after day, at all hours. A mobile radar fines in
sessions of a few hours, on scattered days. Only a place with that second
pattern gives a radar, of kind ``mobile_recurring``; the rules and the places
they sort are below. A fixed camera is left to the sources that publish
cameras, and ``feed.merge`` drops a fines place within ``feed.DUPLICATE_M`` of
one. The data runs about nine months behind (on 2 Oct 2026 the newest quarter
was the last of 2025), so it says where radars stand, not where one stands today.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta

from .. import net
from ..geo import distance_m
from ..model import Radar, SourceResult
from ..provinces import PROVINCES
from ..streets import fold
from .base import Context, Source

log = logging.getLogger(__name__)

PROVINCE = "08"
PORTAL = "https://opendata-ajuntament.barcelona.cat/data"
DATASET = "denuncies_sancions_transit_bcn_detall"
API = f"{PORTAL}/api/3/action/package_show?id={DATASET}"
SQL_API = f"{PORTAL}/api/3/action/datastore_search_sql"
URL = f"{PORTAL}/es/dataset/{DATASET}"
ATTRIBUTION = "Fuente de los datos: Ayuntamiento de Barcelona (CC BY 4.0)"
MONTHS = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"

# Speeding under RGC art. 50.1 and 52.1, every fine band (the portal's codes
# dataset "denuncies_sancions_transit_bcn_codis"). 1225-1227 are other offences.
SPEED_CODES = (*range(1210, 1225), *range(1228, 1233))
CAMERA = "MTO"

# The pattern of a mobile radar, measured on the 53 camera places of the last
# quarter of 2025. A session is a day with fines; a session that runs past
# midnight (fines until 21:00 or later, then from before 03:00 the next day)
# counts once.
# - Fixed cameras fine at all hours: on 64 to 88 of their days the fines span 12
#   hours or more (Via Augusta 331, the B-10 and B-20, the Glòries tunnel). So do
#   cameras that worked only part of the quarter: the Ronda del Mig from 20 Nov
#   (21 to 41 such days), Ronda Litoral (Llobregat) 96 on 1-10 Oct (8 of 10), the
#   two B-10 161 cameras on 12-19 Dec (7 of 8).
ALL_DAY_H = 12
MAX_ALL_DAY_SHARE = 0.25  # more of its days spanning ALL_DAY_H: a camera
# - Low-traffic fixed cameras fine one or two cars a day, but day after day:
#   Gran Via 329 (60 days, 15 in a row), Aragó 174 (51, 9 in a row), Diagonal 579
#   (every day 1-28 Oct). A mobile radar is gone the next day, or stays a week at
#   most: B-10 (Besòs) 176 fined on 15 days, at most 7 in a row, 1.5 h a day.
MAX_RUN_DAYS = 7  # more days in a row: a camera
# - And a camera is there most days. Muntaner 158 fined on 75 of 92 days, never
#   more than 6 in a row and on 16 of them over 12 hours.
MAX_DAYS_SHARE = 0.5  # fines on more of the quarter's days: a camera
# - A session of one fine is no radar standing there (Carrer A Zona Franca 93: one
#   fine on each of two days), and one session is no recurring spot.
MIN_SESSION_FINES = 2
MIN_SESSIONS = 2
# Places of one street this close are one spot: one zone, kept at the place that
# sorts first, so it stays put while that place is in the data.
MERGE_M = 200


@dataclass(frozen=True)
class Quarter:
    resource: str  # datastore resource id
    year: int
    quarter: int

    @property
    def first(self) -> date:
        return date(self.year, 3 * self.quarter - 2, 1)

    @property
    def last(self) -> date:
        if self.quarter == 4:
            return date(self.year, 12, 31)
        return date(self.year, 3 * self.quarter + 1, 1) - timedelta(days=1)

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def label(self) -> str:
        months = MONTHS.split()
        first, last = months[self.first.month - 1], months[self.last.month - 1]
        return f"{first} a {last} de {self.year}"


@dataclass(frozen=True)
class Day:
    """One place's fines on one day, with the first and last time (hours)."""

    day: date
    fines: int
    first: float | None
    last: float | None

    @property
    def span(self) -> float:
        return (self.last - self.first) if self.first is not None and self.last is not None else 0


def newest_quarter(package: bytes) -> tuple[Quarter, str | None]:
    """The newest quarterly CSV in the datastore, and the dataset's last update day."""
    data = json.loads(package)
    if not data.get("success"):
        raise ValueError(f"CKAN package_show for {DATASET} did not succeed")
    result = data["result"]
    quarters = []
    for r in result.get("resources", []):
        m = re.match(r"(\d{4})_([1-4])t_", str(r.get("name", "")))
        if m and r.get("datastore_active") and str(r.get("format", "")).upper() == "CSV":
            quarters.append(Quarter(r["id"], int(m.group(1)), int(m.group(2))))
    if not quarters:
        raise ValueError(f"no quarterly CSV in the datastore of {DATASET}")
    updated = str(result.get("metadata_modified") or "")[:10] or None
    return max(quarters, key=lambda q: (q.year, q.quarter)), updated


def query(resource: str) -> str:
    """The SQL that groups one quarter's camera speed fines by place and day."""
    codes = ",".join(f"'{c}'" for c in SPEED_CODES)
    hour = "NULLIF(trim(\"Hora_Infraccio\"), '')"
    return (
        'SELECT "Nom_Carrer" street, "Num_Carrer" num, "Latitud_WGS84" lat, '
        '"Longitud_WGS84" lon, "Data_Infraccio" d, count(*) fines, '
        f"min({hour}) h0, max({hour}) h1 "
        f'FROM "{resource}" WHERE "MITJA_IMPOSICIO" = \'{CAMERA}\' '
        f'AND "Infraccio_Codi" IN ({codes}) GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 5'
    )


def sql_url(resource: str) -> str:
    return f"{SQL_API}?{urllib.parse.urlencode({'sql': query(resource)})}"


def _in_barcelona(lat: float, lon: float) -> bool:
    south, west, north, east = PROVINCES[PROVINCE][1]
    return south <= lat <= north and west <= lon <= east


def _hours(text: str | None) -> float | None:
    """'234400' -> 23.73; blank -> None."""
    text = (text or "").strip()
    if not re.fullmatch(r"\d{4,6}", text):
        return None
    return int(text[:2]) + int(text[2:4]) / 60


def sessions(days: list[Day]) -> list[tuple[date, int]]:
    """(first day, fines) of each session: a day, or two when fines run past midnight."""
    out: list[tuple[date, int]] = []
    for prev, day in zip([None, *days], days, strict=False):
        joined = (
            prev is not None
            and day.day - prev.day == timedelta(days=1)
            and prev.last is not None
            and prev.last >= 21
            and day.first is not None
            and day.first < 3
        )
        if joined:
            out[-1] = (out[-1][0], out[-1][1] + day.fines)
        else:
            out.append((day.day, day.fines))
    return out


def camera_like(days: list[Day], period_days: int) -> str:
    """Why a place's days look like a camera's, or '' when they look like a
    mobile radar's."""
    run = longest = 1
    for prev, day in zip(days, days[1:], strict=False):
        run = run + 1 if day.day - prev.day == timedelta(days=1) else 1
        longest = max(longest, run)
    all_day = sum(d.span >= ALL_DAY_H for d in days)
    if all_day > MAX_ALL_DAY_SHARE * len(days):
        return f"fines over {ALL_DAY_H} h or more on {all_day} of {len(days)} days"
    if longest > MAX_RUN_DAYS:
        return f"fines on {longest} days in a row"
    if len(days) > MAX_DAYS_SHARE * period_days:
        return f"fines on {len(days)} of {period_days} days"
    return ""


@dataclass(frozen=True)
class Place:
    street: str
    num: str
    lat: float
    lon: float
    days: tuple[Day, ...]

    @property
    def label(self) -> str:
        return (
            f"{self.street} {int(self.num)}"
            if self.num.isdigit() and int(self.num)
            else self.street
        )


def places(answer: bytes) -> list[Place]:
    """The places of a datastore answer with their days, in the answer's order.
    Raises when the answer is no SQL result."""
    data = json.loads(answer)
    if not data.get("success"):
        raise ValueError(f"datastore SQL failed: {data.get('error')}")
    grouped: dict[tuple, list[Day]] = {}
    for rec in data["result"]["records"]:
        key = (rec["street"], rec["num"], rec["lat"], rec["lon"])
        day = Day(
            date.fromisoformat(rec["d"]), int(rec["fines"]), _hours(rec["h0"]), _hours(rec["h1"])
        )
        grouped.setdefault(key, []).append(day)
    found = []
    for (street, num, lat, lon), days in grouped.items():
        try:
            point = float(lat), float(lon)
        except (TypeError, ValueError):
            point = None
        if point is None or not _in_barcelona(*point):
            log.warning("barcelona_multas: place without a position skipped: %s %s", street, num)
            continue
        street = " ".join(str(street).split())
        found.append(
            Place(street, str(num).strip(), *point, tuple(sorted(days, key=lambda d: d.day)))
        )
    return found


def parse(answer: bytes, quarter: Quarter, updated: str | None = None) -> SourceResult:
    """One radar per spot where a mobile radar fined in MIN_SESSIONS sessions or
    more. Raises when the answer holds no camera place at all, so a broken answer
    never empties the feed (the registry keeps the last good result instead)."""
    found = places(answer)
    if not found:
        raise ValueError(f"no camera place in the speed fines of {quarter.label()}")
    mobile: list[tuple[Place, set[date]]] = []
    for p in found:
        why = camera_like(list(p.days), quarter.days)
        if why:
            log.info("barcelona_multas: %s fines like a camera (%s); no zone", p.label, why)
            continue
        held = {day for day, n in sessions(list(p.days)) if n >= MIN_SESSION_FINES}
        if len(held) < MIN_SESSIONS:
            log.info(
                "barcelona_multas: %s: %d session(s) of %d fines or more; no zone",
                p.label,
                len(held),
                MIN_SESSION_FINES,
            )
            continue
        mobile.append((p, held))
    log.info(
        "barcelona_multas: %s, %d camera places with a position, %d where a mobile radar "
        "fined in %d sessions or more",
        quarter.label(),
        len(found),
        len(mobile),
        MIN_SESSIONS,
    )
    attribution = f"{ATTRIBUTION}, multas de tráfico de {quarter.label()}"
    if updated:
        attribution += f", actualizado {updated}"
    radars = []
    for p, held in _merge(mobile):
        radars.append(
            Radar(
                id=f"barcelona_multas-{fold(f'{p.street} {p.num}').replace(' ', '-')}",
                source="barcelona_multas",
                kind="mobile_recurring",
                name=f"Radar móvil frecuente {p.label}",
                lat=p.lat,
                lon=p.lon,
                radius_m=500,
                url=URL,
                attribution=f"{attribution}; multas aquí en {len(held)} días de {quarter.days}",
                province=PROVINCE,
            )
        )
    return SourceResult(radars=radars, updated=updated)


def _merge(mobile: list[tuple[Place, set[date]]]) -> list[tuple[Place, set[date]]]:
    """One spot per street stretch: places of one street within MERGE_M of each
    other (in a chain) become the place that sorts first, with the days of all."""
    ordered = sorted(mobile, key=lambda pc: (pc[0].street, pc[0].num))
    groups: list[list[tuple[Place, set[date]]]] = []
    for pc in ordered:
        near = [
            g
            for g in groups
            if any(
                q.street == pc[0].street
                and distance_m((q.lat, q.lon), (pc[0].lat, pc[0].lon)) <= MERGE_M
                for q, _ in g
            )
        ]
        for g in near[1:]:
            near[0].extend(g)
            groups.remove(g)
        if near:
            near[0].append(pc)
        else:
            groups.append([pc])
    merged = []
    for g in groups:
        first = min(g, key=lambda pc: (pc[0].street, pc[0].num))[0]
        if len(g) > 1:
            log.info(
                "barcelona_multas: %s takes in %s",
                first.label,
                [q.label for q, _ in g if q != first],
            )
        merged.append((first, set().union(*(days for _, days in g))))
    return merged


def fetch(ctx: Context) -> SourceResult:
    quarter, updated = newest_quarter(net.cached_get(API, max_age_s=ctx.max_age_s))
    answer = net.cached_get(sql_url(quarter.resource), max_age_s=ctx.max_age_s)
    return parse(answer, quarter, updated)


SOURCE = Source(
    key="barcelona_multas",
    fetch=fetch,
    attribution=ATTRIBUTION + ", detalle de las denuncias y sanciones de tráfico",
    licence="CC BY 4.0",
    max_age_s=7 * 86_400,  # a new quarter appears a few times a year
    provinces=frozenset({PROVINCE}),
)
