"""Where Madrid's mobile radars stood, from the city's traffic-fines open data.

Dataset 210104 "Multas de circulación: detalle" on https://datos.madrid.es
(CC BY 4.0, cited like ``madrid``): one CSV a month with every fine the city
processed, found through the CKAN API by its "Detalle. <mes> <año>" resource. A
speed fine names its place, without coordinates. A place coded ``N<number>
<street>`` is a street number ("P.SM.CABEZA_N115" and "PO SANTA MARIA CABEZA,
115" in the same files). A place coded ``F<number>`` is a lamp post ("F040 AV
PUERTA DE HIERRO" next to "AV PUERTA DE HIERRO FAROLA 40"); the city publishes
no lamp post by its number, so those places get no zone. The data runs about
seven months behind: on 2 Oct 2026 the newest month was February 2026.

A place gets a zone when it fined in ``MIN_MONTHS`` of the last ``MONTHS``
months, in the hours a mobile radar works rather than at all hours. It is placed
at its number in the city's official street register (dataset 213605, "Relación
de direcciones vigentes, con coordenadas", CC BY 4.0): the portal point the city
gives that address. A place whose street or number the register does not hold
is skipped and logged, never guessed.

Each month's file is about 60 MB and the register about 35 MB; both are read as
they arrive. What a month says about its places and where each place stands are
kept, a few kilobytes, under ``sources/madrid_multas/`` in the cache folder,
which the published feed keeps between runs. So a month is downloaded once, and
the register only when a new place needs it.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import urllib.request
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .. import net
from ..geo import distance_m, utm_to_wgs84
from ..model import Radar, SourceResult
from ..streets import fold
from .base import Context, Source

log = logging.getLogger(__name__)

PROVINCE = "28"
DATASET = "210104-0-multas-circulacion-detalle"
API = f"https://datos.madrid.es/api/3/action/package_show?id={DATASET}"
URL = f"https://datos.madrid.es/dataset/{DATASET}"
REGISTER = "213605-0-callejero-oficial-madrid"
REGISTER_API = f"https://datos.madrid.es/api/3/action/package_show?id={REGISTER}"
ATTRIBUTION = "Origen de los datos: Ayuntamiento de Madrid (CC BY 4.0)"
VERSION = 2  # of the kept month summaries; another one is read again

# Recurrence measured over Sep 2025 to Feb 2026: 124 places coded "N", 91 of them
# in one month only, 16 in two, 9 in three, 4 in four, 2 in five and 2 in all six.
# Over the last 2 months 12 of 65 places recur, over 3 months 16 of 72, over 4
# months 20 of 84, over 6 months 33 of 124. Six months keep the most places that
# came back, at a cost of the oldest data being about 13 months old.
MONTHS = 6
MIN_MONTHS = 2
# A mobile radar works in sessions of a few hours. Over those six months no recurring "N"
# place fined in more than 14 distinct hours of the day, all months together; a
# fixed camera fines at every hour. A place that fines in this many distinct
# hours within one month is a camera, not a mobile radar's spot.
CAMERA_HOURS = 18
# Places of one street this close are one spot (Avenida de Valladolid 51 and 55,
# 25 m; Juan de Herrera 1, 2 and 6): one zone, at the place with the lowest
# number, so it stays put while that place is in the data.
MERGE_M = 200
# One number can be several points in the register (a portal, a garage, a
# plot). Farther apart than this, it is no single place: skipped.
SAME_NUMBER_M = 50

# The place field keeps 20 characters of the street ("ALCALDE SAINZ BARAND"),
# and a name cut after a space loses that space too: a street written in 19 or
# 20 characters may continue in the register ("PO GENERAL MARTINEZ" is "Paseo del
# General Martínez Campos").
CUT_AT = 19
# Street types as the fines write them; no type is a "Calle".
TYPES = {
    "AV": "avenida",
    "CR": "carretera",
    "CU": "cuesta",
    "GL": "glorieta",
    "PO": "paseo",
    "PZ": "plaza",
    "RD": "ronda",
    "TR": "travesia",
}
WORDS = {"fco": "francisco"}
SMALL = {"a", "d", "de", "del", "el", "la", "las", "los", "y"}
ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII"}

_PLACE = re.compile(r"^N(\d{1,4})(\S*)\s+(\S.*)$")
_LAMP_POST = re.compile(r"^F(L|\d)")
MONTH_NAMES = (
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"
).split()


@dataclass(frozen=True)
class Month:
    year: int
    month: int
    url: str
    resource: str
    modified: str  # the resource's last change; a new one is read again

    def label(self) -> str:
        return f"{MONTH_NAMES[self.month - 1]} de {self.year}"


def months(package: bytes) -> list[Month]:
    """Every monthly "Detalle" CSV of a CKAN package_show answer, oldest first."""
    data = json.loads(package)
    if not data.get("success"):
        raise ValueError(f"CKAN package_show for {DATASET} did not succeed")
    found: dict[tuple[int, int], Month] = {}
    for r in data["result"].get("resources", []):
        desc = fold(str(r.get("description", "")))
        m = re.fullmatch(r"detalle (\w+) (\d{4})", desc)
        if not m or m.group(1) not in MONTH_NAMES or str(r.get("format", "")).upper() != "CSV":
            continue
        year, month = int(m.group(2)), MONTH_NAMES.index(m.group(1)) + 1
        modified = str(r.get("last_modified") or r.get("created") or "")
        found[year, month] = Month(year, month, r["url"], r["id"], modified)
    if not found:
        raise ValueError(f"no monthly detail CSV in {DATASET}")
    return [found[k] for k in sorted(found)]


def parse_month(lines: Iterable[str]) -> dict[str, dict]:
    """{place: {"fines": n, "limits": {limit: n}, "hours": [hour, ...]}} for the
    speed fines at places coded "N<number> <street>". Raises when the header lacks
    a column it needs or the month holds no speed fine at all (a changed file)."""
    rows = csv.reader(lines, delimiter=";")
    header = [h.strip() for h in next(rows, [])]
    needed = ("LUGAR", "HORA", "VEL_LIMITE", "VEL_CIRCULA")
    if any(c not in header for c in needed):
        raise ValueError(f"fines CSV header lacks {needed}: {header}")
    where, hour, limit, speed = (header.index(c) for c in needed)
    places: dict[str, dict] = {}
    speeding = lamp_posts = 0
    for row in rows:
        if len(row) <= max(where, hour, limit, speed):
            continue
        kmh, driven = row[limit].strip(), row[speed].strip()
        if not (kmh.isdigit() and driven.isdigit()):
            continue
        speeding += 1
        place = " ".join(row[where].split())
        if not _PLACE.match(place):
            lamp_posts += bool(_LAMP_POST.match(place))
            continue
        entry = places.setdefault(place, {"fines": 0, "limits": {}, "hours": []})
        entry["fines"] += 1
        entry["limits"][kmh] = entry["limits"].get(kmh, 0) + 1
        h = re.match(r"\s*(\d{1,2})[.:]", row[hour])
        if h and int(h.group(1)) not in entry["hours"]:
            entry["hours"] = sorted([*entry["hours"], int(h.group(1))])
    if not speeding:
        raise ValueError("the fines CSV holds no speed fine")
    log.info("madrid_multas: %d speed fines at lamp posts get no zone", lamp_posts)
    return places


def _folder() -> Path:
    return net.cache_dir() / "sources" / "madrid_multas"


def _keep(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        tmp.replace(path)
    except OSError as exc:  # an unwritable cache must not fail a download that worked
        log.warning("could not keep %s: %s", path, exc)


def _stream(url: str):
    """A CSV as text as it arrives: a whole file in memory would be 35 to 60 MB."""
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    response = urllib.request.urlopen(request, timeout=300)
    return io.TextIOWrapper(response, encoding="latin-1", newline="")


def _download(url: str) -> dict[str, dict]:
    with _stream(url) as text:
        return parse_month(text)


def month_places(m: Month) -> dict[str, dict]:
    """A month's places, from the kept summary while the portal's file is the same."""
    path = _folder() / f"{m.year}-{m.month:02d}.json"
    try:
        kept = json.loads(path.read_text("utf-8"))
        if (kept.get("version"), kept.get("resource"), kept.get("modified")) == (
            VERSION,
            m.resource,
            m.modified,
        ):
            return kept["places"]
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError) as exc:
        log.warning("ignoring a damaged summary of %s: %s", m.label(), exc)
    places = _download(m.url)
    _keep(
        path, {"version": VERSION, "resource": m.resource, "modified": m.modified, "places": places}
    )
    return places


# ---- the street register ----------------------------------------------------


@dataclass(frozen=True)
class Street:
    kind: str  # "calle", folded
    name: str  # "josefa valcarcel", folded
    label: str  # "Calle de Josefa Valcárcel"


def _words(text: str) -> tuple[str, ...]:
    return tuple(WORDS.get(w, w) for w in fold(text).split() if w not in SMALL)


def written(street: str) -> tuple[str, tuple[str, ...]]:
    """'AV FCO J SAENZ OIZA' -> ('avenida', ('francisco', 'j', 'saenz', 'oiza'))."""
    first, _, rest = street.partition(" ")
    if first in TYPES and rest:
        return TYPES[first], _words(rest)
    return "calle", _words(street)


def same_street(street: str, kind: str, name: str) -> bool:
    """Whether the fines' ``street`` can be the register's street: same type, the
    same words (a written initial matches a word; a name cut at CUT_AT may
    continue), and one word written in full."""
    want_kind, words = written(street)
    mapped = _words(name)
    if kind != want_kind or not words:
        return False
    cut = len(street) >= CUT_AT
    if cut:
        mapped = mapped[: len(words)]
    if len(mapped) != len(words):
        return False
    for i, (a, b) in enumerate(zip(words, mapped, strict=True)):
        last = cut and i == len(words) - 1
        if a != b and not ((len(a) == 1 or last) and b.startswith(a)):
            return False
    return any(a == b for a, b in zip(words, mapped, strict=True))


def _label(clase: str, particle: str, name: str) -> str:
    """'CALLE', 'DEL', 'ALFONSO XII' -> 'Calle del Alfonso XII'."""
    words = []
    for w in name.split():
        if w in ROMAN:
            words.append(w)
        elif fold(w) in SMALL:
            words.append(w.lower())
        else:
            words.append(w.capitalize() if "'" not in w else w.title())
    return " ".join(p for p in (clase.capitalize(), particle.lower(), " ".join(words)) if p)


def register_csv(package: bytes) -> tuple[str, str]:
    """(URL, last change) of the register's current addresses with coordinates."""
    data = json.loads(package)
    if not data.get("success"):
        raise ValueError(f"CKAN package_show for {REGISTER} did not succeed")
    for r in data["result"].get("resources", []):
        desc = fold(str(r.get("description", "")))
        if "direcciones vigentes" in desc and "coordenadas" in desc and r.get("format") == "CSV":
            return r["url"], str(r.get("last_modified") or "")
    raise ValueError(f"no current addresses CSV in {REGISTER}")


def read_register(lines: Iterable[str], numbers: set[int]) -> tuple[list[Street], dict]:
    """Every street of the register, and the points of the wanted ``numbers``:
    {(kind, name, number): [(qualifier, type, (lat, lon))]}."""
    streets: dict[tuple[str, str], Street] = {}
    points: dict[tuple[str, str, int], list] = {}
    for row in csv.DictReader(lines, delimiter=";"):
        kind, name = fold(row["VIA_CLASE"]), fold(row["VIA_NOMBRE"])
        if (kind, name) not in streets:
            label = _label(row["VIA_CLASE"], row["VIA_PAR"], row["VIA_NOMBRE_ACENTOS"])
            streets[kind, name] = Street(kind, name, label)
        number = row["NUMERO"].strip()
        if (
            row["CLASE_APP"].strip() != "NUMERO"
            or not number.isdigit()
            or int(number) not in numbers
        ):
            continue
        x, y = (float(row[c].replace(",", ".")) for c in ("UTMX_ETRS", "UTMY_ETRS"))
        point = utm_to_wgs84(x, y, 30)
        entry = (row["CALIFICADOR"].strip(), row["TIPO_NDP"].strip(), point)
        points.setdefault((kind, name, int(number)), []).append(entry)
    if not streets:
        raise ValueError("the street register holds no address")
    return list(streets.values()), points


def locate(place: str, streets: list[Street], points: dict) -> tuple[Street, tuple] | str:
    """(street, (lat, lon)) of a place, or why it cannot be placed."""
    m = _PLACE.match(place)
    number, suffix, written_street = int(m.group(1)), m.group(2), m.group(3)
    if not number or suffix:
        return f"{number}{suffix} is no house number"
    found = [s for s in streets if same_street(written_street, s.kind, s.name)]
    if len(found) != 1:
        return "street not in the register" if not found else "several streets match"
    street = found[0]
    rows = points.get((street.kind, street.name, number), [])
    plain = [r for r in rows if not r[0]] or rows
    chosen = [p for q, t, p in plain if t == "PORTAL"] or [p for _, _, p in plain]
    if not chosen:
        return f"no number {number} in the register"
    if max(distance_m(a, b) for a in chosen for b in chosen) > SAME_NUMBER_M:
        return f"number {number} is several places in the register"
    lat = math.fsum(p[0] for p in chosen) / len(chosen)
    lon = math.fsum(p[1] for p in chosen) / len(chosen)
    return street, (round(lat, 7), round(lon, 7))


# ---- the radars -------------------------------------------------------------


def recurring(window: list[dict[str, dict]]) -> dict[str, tuple[int, Counter]]:
    """{place: (months it fined in, fines by limit)} for the places that fined in
    MIN_MONTHS months and never at camera hours."""
    seen: dict[str, tuple[int, Counter]] = {}
    camera: set[str] = set()
    for places in window:
        for place, entry in places.items():
            n, limits = seen.get(place, (0, Counter()))
            seen[place] = (n + 1, limits + Counter(entry["limits"]))
            if len(entry["hours"]) >= CAMERA_HOURS:
                camera.add(place)
    for place in sorted(camera):
        log.info(
            "madrid_multas: %s fines at %d hours or more, like a camera; no zone",
            place,
            CAMERA_HOURS,
        )
    return {p: v for p, v in seen.items() if v[0] >= MIN_MONTHS and p not in camera}


def place_all(wanted: list[str], kept: dict, register: tuple[str, str] | None, read) -> dict:
    """Records for the ``wanted`` places: a kept one as it was (its position and
    limit never move while it is kept), the others from the register, read with
    ``read(url, numbers)`` only when one is missing."""
    records = dict(kept.get("places", {}))
    url, modified = register or ("", "")
    todo = [
        p
        for p in wanted
        if p not in records or ("skip" in records[p] and records[p].get("register") != modified)
    ]
    if todo and register is not None:
        numbers = {int(_PLACE.match(p).group(1)) for p in todo}
        streets, points = read(url, numbers)
        for p in todo:
            where = locate(p, streets, points)
            if isinstance(where, str):
                records[p] = {"skip": where, "register": modified}
            else:
                street, (lat, lon) = where
                records[p] = {
                    "street": street.label,
                    "key": [street.kind, street.name],
                    "number": int(_PLACE.match(p).group(1)),
                    "lat": lat,
                    "lon": lon,
                }
    return records


def to_radars(
    window: list[Month],
    places: dict[str, tuple[int, Counter]],
    records: dict,
    in_months: dict[str, set[int]],
    updated: str | None = None,
) -> tuple[list[Radar], list[str]]:
    """One radar per spot, and the places skipped. Places of one street within
    MERGE_M of each other (in a chain) are one spot, at the lowest number."""
    placed = sorted(
        (p for p in places if "lat" in records.get(p, {})),
        key=lambda p: (records[p]["key"], records[p]["number"], p),
    )
    skipped = sorted(p for p in places if p not in placed)
    for p in skipped:
        why = records.get(p, {}).get("skip", "street register not read")
        log.warning("madrid_multas: could not place %s (%s); skipped", p, why)
    groups: list[list[str]] = []
    for p in placed:
        r = records[p]
        near = [
            g
            for g in groups
            if any(
                records[q]["key"] == r["key"]
                and distance_m((records[q]["lat"], records[q]["lon"]), (r["lat"], r["lon"]))
                <= MERGE_M
                for q in g
            )
        ]
        for g in near[1:]:
            near[0].extend(g)
            groups.remove(g)
        if near:
            near[0].append(p)
        else:
            groups.append([p])
    attribution = (
        f"{ATTRIBUTION}: multas de circulación de {window[0].label()} a {window[-1].label()}"
        + (f", actualizado {updated}" if updated else "")
        + ", y callejero oficial"
    )
    radars = []
    for group in groups:
        first = min(group, key=lambda p: (records[p]["number"], p))
        r = records[first]
        if len(group) > 1:
            log.info("madrid_multas: %s takes in %s", first, sorted(set(group) - {first}))
        seen = len(set().union(*(in_months[p] for p in group)))
        radars.append(
            Radar(
                id=f"madrid_multas-{fold(first).replace(' ', '-')}",
                source="madrid_multas",
                kind="mobile_recurring",
                name=f"Radar móvil frecuente {r['street']} {r['number']}",
                lat=r["lat"],
                lon=r["lon"],
                radius_m=500,
                url=URL,
                attribution=f"{attribution}; multas aquí en {seen} de {len(window)} meses",
                maxspeed=r.get("limit"),
                province=PROVINCE,
            )
        )
    return radars, skipped


def _read_register(url: str, numbers: set[int]):
    with _stream(url) as text:
        return read_register(text, numbers)


def fetch(ctx: Context) -> SourceResult:
    window = months(net.cached_get(API, max_age_s=ctx.max_age_s))[-MONTHS:]
    month_data = [month_places(m) for m in window]
    places = recurring(month_data)
    in_months = {p: {i for i, d in enumerate(month_data) if p in d} for p in places}
    path = _folder() / "places.json"
    try:
        kept = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        kept = {}
    except (OSError, ValueError) as exc:
        log.warning("ignoring the damaged kept places of Madrid: %s", exc)
        kept = {}
    records = kept.get("places", {})
    missing = [p for p in places if p not in records or "skip" in records[p]]
    register = None
    if missing:
        register = register_csv(net.cached_get(REGISTER_API, max_age_s=ctx.max_age_s))
    records = place_all(sorted(places), kept, register, _read_register)
    for p, (_, limits) in places.items():
        # the limit a place is first placed with stays, so its zone does not move
        if "lat" in records[p] and "limit" not in records[p]:
            records[p]["limit"] = int(max(limits, key=lambda k: (limits[k], int(k))))
    _keep(path, {"places": records})
    updated = window[-1].modified[:10] or None
    radars, skipped = to_radars(window, places, records, in_months, updated)
    log.info(
        "madrid_multas: %s to %s, %d places in %d months or more: %d placed, %d skipped, %d zones",
        window[0].label(),
        window[-1].label(),
        len(places),
        MIN_MONTHS,
        len(places) - len(skipped),
        len(skipped),
        len(radars),
    )
    if not radars:
        raise ValueError("no recurring place of the Madrid fines could be placed")
    return SourceResult(radars=radars, updated=updated)


SOURCE = Source(
    key="madrid_multas",
    fetch=fetch,
    attribution=ATTRIBUTION + ": multas de circulación (detalle) y callejero oficial",
    licence="CC BY 4.0",
    max_age_s=7 * 86_400,  # a new month appears about once a month
    provinces=frozenset({PROVINCE}),
)
