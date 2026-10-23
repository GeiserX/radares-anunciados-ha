"""The daily mobile-radar plan of the city of Donostia / San Sebastián.

The council's page "Ubicación del radar móvil" names today's streets, one
``<span class="label">`` each, after "Durante el MM/DD/YYYY" (month first). On a
day without a plan it says "No hay ninguna ubicación planificada para hoy." The
map beside it loads ``javascript_geo.js``, generated with the page, which holds
one LineString per street (WGS84) in a JavaScript object, not JSON: titles are
single-quoted. Each named street with a line is covered with circles; a street
without one is looked up in CartoCiudad and Nominatim (``geocode``), and
skipped and reported if they don't know it either. A plan is valid on its day only; the
next day its streets go dormant like any periodic list's (``feed.remember``).

Reuse terms: the council's own, as for its fixed radars (``donostia.LICENCE``).
"""

from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
from datetime import date, timedelta

from .. import net
from ..geo import cover
from ..geocode import CARTOCIUDAD_CREDIT, Geocoder, Town, geometry_credit
from ..model import Announced, Radar, SourceResult, WeeklyList
from .base import Context, Source
from .donostia import LICENCE

log = logging.getLogger(__name__)

PAGE = (
    "https://www.donostia.eus/info/ciudadano/radar_movil.nsf/fwHome"
    "?ReadForm=&idioma=cas&id=A434305381910"
)
SCRIPT = "https://www.donostia.eus/info/ciudadano/radar_movil.nsf/javascript_geo.js"
ATTRIBUTION = "Ayuntamiento de Donostia / San Sebastián, ubicación del radar móvil"
PROVINCE = "20"
PLACE = "Donostia"
TOWN = Town("Donostia/San Sebastián", "20069", (43.24, -2.09, 43.34, -1.91))
ENCODING = "cp1252"  # the page says ISO-8859-1; browsers read that as windows-1252

_NO_PLAN = re.compile(r"No hay ninguna ubicaci.n planificada", re.IGNORECASE)
_DAY = re.compile(r"Durante el (\d{1,2})/(\d{1,2})/(\d{4})")
_LABEL = re.compile(r'<span class="label">(.*?)</span>', re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _key(name: str) -> str:
    """Compare street names without case, accents or extra spaces."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().casefold()


def parse_page(page: str) -> tuple[date | None, list[str]]:
    """(the plan's day, its streets); (None, []) on a day without a plan.
    A page that says neither raises: its layout changed."""
    if _NO_PLAN.search(page):
        return None, []
    m = _DAY.search(page)
    if not m:
        raise ValueError("donostia_movil: the page names no day and no 'no plan' notice")
    month, day, year = (int(g) for g in m.groups())
    streets: list[str] = []
    for label in _LABEL.findall(page[m.end() :]):
        street = _text(label)
        if street and street not in streets:
            streets.append(street)
    return date(year, month, day), streets


def js_object(script: str, name: str) -> dict:
    """The object literal assigned to ``var <name>``, read as JSON: single-quoted
    strings become double-quoted, trailing commas and comments go."""
    m = re.search(rf"\bvar\s+{re.escape(name)}\s*=\s*\{{", script)
    if not m:
        raise ValueError(f"no 'var {name} = {{' in the script")
    out: list[str] = []
    depth = 0
    i = m.end() - 1
    while i < len(script):
        c = script[i]
        if script.startswith("//", i):
            end = script.find("\n", i)
            i = len(script) if end < 0 else end
            continue
        if script.startswith("/*", i):
            end = script.find("*/", i + 2)
            if end < 0:
                raise ValueError(f"'var {name}' has a comment that never closes")
            i = end + 2
            continue
        if c in "\"'":
            j, chars = i + 1, []
            try:
                while script[j] != c:
                    if script[j] == "\\":
                        j += 1
                        chars.append(script[j] if c == "'" else "\\" + script[j])
                    else:
                        chars.append(script[j])
                    j += 1
            except IndexError:
                raise ValueError(f"'var {name}' has a string that never closes") from None
            text = "".join(chars)
            out.append(json.dumps(text) if c == "'" else f'"{text}"')
            i = j + 1
            continue
        if c in "}]":
            while out and out[-1].isspace():
                out.pop()
            if out and out[-1] == ",":
                out.pop()
        out.append(c)
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads("".join(out))
        i += 1
    raise ValueError(f"'var {name}' never closes")


def parse_lines(script: str) -> dict[str, list[list[tuple[float, float]]]]:
    """Street title (Spanish) -> its lines as (lat, lon) points."""
    lines: dict[str, list[list[tuple[float, float]]]] = {}
    for feature in js_object(script, "puntos").get("features", []):
        geometry = feature.get("geometry") or {}
        kind, coords = geometry.get("type"), geometry.get("coordinates") or []
        parts = [coords] if kind == "LineString" else coords if kind == "MultiLineString" else []
        title = _text((feature.get("properties") or {}).get("tituloCas", ""))
        for part in parts:
            line = [(float(p[1]), float(p[0])) for p in part]
            if title and line:
                lines.setdefault(title, []).append(line)
    return lines


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _key(text)).strip("-")


def to_radars(
    day: date,
    streets: list[str],
    lines: dict[str, list],
    radius_m: float,
    geocoder: Geocoder | None = None,
    methods: dict[str, str] | None = None,
) -> tuple[list[Radar], list[str]]:
    """Circles along every street of the plan that has a line, from the council
    or else from ``geocoder``; and the streets without one. How each placed street
    was found goes to ``methods``. A line the page does not name is left out."""
    by_key = {_key(title): found for title, found in lines.items()}
    extra = set(by_key) - {_key(s) for s in streets}
    if extra:
        log.info("donostia_movil: %d line(s) not named on the page, left out", len(extra))
    radars: list[Radar] = []
    skipped: list[str] = []
    for street in streets:
        found, method = by_key.get(_key(street), []), "council"
        if not found and geocoder is not None:
            answer = geocoder.street(street, None, [])
            if answer:
                found, method = answer.lines, answer.method
        centres = cover(found, radius_m)
        if not centres:
            log.warning("donostia_movil: no line for %s on %s; skipped", street, day)
            skipped.append(street)
            continue
        if methods is not None:
            methods[f"{street} ({PLACE})"] = method
        for n, (lat, lon) in enumerate(centres):
            radars.append(
                Radar(
                    id=f"donostia_movil-{day.isoformat()}-{_slug(street)}-{n}",
                    source="donostia_movil",
                    kind="mobile_announced",
                    name=f"Radar anunciado {street} ({PLACE})",
                    lat=lat,
                    lon=lon,
                    radius_m=round(radius_m),
                    valid_from=day,
                    valid_to=day,
                    url=PAGE,
                    attribution=geometry_credit(ATTRIBUTION, method),
                    province=PROVINCE,
                )
            )
    return radars, skipped


def fetch(ctx: Context) -> SourceResult:
    monday = ctx.day - timedelta(days=ctx.day.weekday())
    # Page and script change together each day: both are read fresh, never one
    # from the cache and the other from today.
    day, streets = parse_page(net.get(PAGE).decode(ENCODING, "replace"))
    if day is None:
        # The council says there is no plan today: a list found, with no street.
        log.info("donostia_movil: no mobile radar planned for today")
        return SourceResult(lists=[WeeklyList("donostia_movil", monday, published=ctx.day)])
    lines = parse_lines(net.get(SCRIPT).decode(ENCODING, "replace")) if streets else {}
    placed: dict[str, str] = {}
    radars, skipped = to_radars(
        day, streets, lines, ctx.radius.street_m(None), Geocoder(TOWN), placed
    )
    status = WeeklyList(
        "donostia_movil",
        monday,
        published=day,
        streets=[Announced(s, PLACE) for s in streets],
        skipped=[Announced(s, PLACE) for s in skipped],
        placed=placed,
    )
    log.info("donostia_movil: plan for %s, %d streets", day, len(streets))
    return SourceResult(radars=radars, lists=[status])


SOURCE = Source(
    key="donostia_movil",
    fetch=fetch,
    attribution=f"{ATTRIBUTION}; geometría de una calle sin línea: {CARTOCIUDAD_CREDIT}, "
    "© OpenStreetMap",
    licence=f"{LICENCE}. A street the council gives no line for: CartoCiudad (IGN), CC BY 4.0, "
    "or OpenStreetMap, ODbL 1.0",
    max_age_s=3_600,
    provinces=frozenset({PROVINCE}),
)
