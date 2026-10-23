"""The weekly mobile-radar list of the Policía Local de Murcia.

The police post the list on X as an image, which a program can't read
without logging in. La Opinión de Murcia republishes it most Mondays as an
article with one ``<li>`` per street; Murcia Actualidad does on some weeks.
We find this week's article through the outlet's sitemap or RSS, read the
"Street, District" pairs, and turn each street into circles near its district
(see ``streets``). A list is valid Monday to Sunday of the week it came out.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
from collections.abc import Callable
from datetime import date, timedelta

from .. import net
from ..geocode import CARTOCIUDAD_CREDIT, Geocoder, Town, geometry_credit
from ..model import Radar, SourceResult
from ..streetnames import expand
from ..streets import STREET_TYPES, Announced, WeeklyList, place, places_query, ways_query
from .base import Context, Source

log = logging.getLogger(__name__)

# Municipality of Murcia with a margin, (south, west, north, east)
BBOX = (37.78, -1.40, 38.10, -0.93)
PROVINCE = "30"
TOWN = Town("Murcia", "30030", BBOX)
CREDIT = "Policía Local de Murcia (lista semanal, leída en {}); geometría © OpenStreetMap"
ATTRIBUTION = CREDIT.format("la prensa local")
# The outlets the police's list is read in, credited by name in each radar.
OUTLETS = {
    "laopiniondemurcia.es": "La Opinión de Murcia",
    "murciaactualidad.com": "Murcia Actualidad",
}

OVERPASS = "https://overpass-api.de/api/interpreter"
LAOPINION_SITEMAP = "https://www.laopiniondemurcia.es/sitemapMonth.xml"
MURCIAACTUALIDAD_RSS = "https://www.murciaactualidad.com/rss/sucesos/"
# La Opinión answers 406 without browser-like headers
BROWSER = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}

_CONNECTOR = re.compile(
    r"^(a su paso por|en el entorno de|en la zona de|junto a|en)\s+"
    r"(la pedanía de\s+|el barrio de\s+)?",
    re.IGNORECASE,
)
_TYPES = "|".join(sorted(STREET_TYPES | {t.capitalize() for t in STREET_TYPES}, key=len))
# "Street, en District" inside running text, for articles without a list
_PAIR = re.compile(
    rf"(?P<street>\b(?:{_TYPES}|Avda\.?|C/|Cno\.?)\s[^,.;:\n]{{2,80}}?)\s*,\s*"
    r"(?P<place>(?:a su paso por|en el entorno de|en la zona de|junto a|en)\s+"
    r"[A-ZÁÉÍÓÚÑ][^,.;:\n()]{1,60}?)\s*(?=[.;,\n]|$| y )",
)


def _clean(fragment: str) -> str:
    """Inline HTML to text. Tags go without a space: La Opinión writes 'A<strong>venida'."""
    text = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    return re.sub(r"[\s\xa0]+", " ", text).strip()


def _street(text: str) -> str:
    return expand(text)


def _place(text: str) -> str:
    return _CONNECTOR.sub("", text.strip(" .")).strip(" .")


def parse_item(text: str) -> Announced | None:
    """'Cno. Tiñosa, RM-F6, Los Dolores' -> Camino Tiñosa / Los Dolores.

    The first part is the street and the last part the district; anything
    between (a road number, a landmark) is dropped.
    """
    parts = [p.strip() for p in text.split(",") if p.strip(" .")]
    if not parts:
        return None
    place = _place(parts[-1]) if len(parts) > 1 else None
    return Announced(_street(parts[0]), place or None)


def parse_article(page: str) -> list[Announced]:
    """The street list of one article, in order, without repeats."""
    items: list[Announced] = []
    # La Opinión: the list is the ul with ft-list--primary. A plain ft-list on
    # the same page holds related headlines, never streets.
    primary = re.search(r'<ul class="ft-list ft-list--primary"[^>]*>(.*?)</ul>', page, re.S)
    if primary:
        for li in re.findall(r"<li\b[^>]*>(.*?)</li>", primary.group(1), re.S):
            item = parse_item(_clean(li))
            if item and item not in items:
                items.append(item)
        return items
    # Anyone else: "Street, en District" pairs in the body text, block by block.
    body = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    body = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\b[^>]*>", "\n", body)
    for chunk in body.split("\n"):
        for match in _PAIR.finditer(_clean(chunk)):
            item = Announced(_street(match.group("street")), _place(match.group("place")))
            if item not in items:
                items.append(item)
    return items


def week_of(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def _published(url: str) -> date | None:
    # La Opinión: /2026/09/28/ ; Murcia Actualidad: /20260817120319032337.html
    m = re.search(r"/(20\d\d)/(\d\d)/(\d\d)/", url) or re.search(r"/(20\d\d)(\d\d)(\d\d)\d{6}", url)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def candidates(index: str, day: date) -> list[tuple[date, str]]:
    """List-article URLs in a sitemap or RSS from the week of ``day``, newest first."""
    start, end = week_of(day)
    found = set()
    for url in re.findall(r"<(?:loc|link)>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)", index):
        is_list = ("laopiniondemurcia.es/murcia/" in url and "radares-semana" in url) or (
            "murciaactualidad.com" in url and "radar-semana" in url
        )
        published = _published(url)
        if is_list and published and start <= published <= end:
            found.add((published, url))
    return sorted(found, reverse=True)


def find_article(day: date) -> tuple[str, date, str] | None:
    """(url, published, html) of this week's list, trying La Opinión first.

    None means the indexes were read and hold no list yet. A download that
    failed raises instead: an empty list would delete this week's zones, while
    a failed run keeps them.
    """
    failed: OSError | None = None
    for index_url in (LAOPINION_SITEMAP, MURCIAACTUALIDAD_RSS):
        try:
            index = net.get(index_url, headers=BROWSER).decode("utf-8", "replace")
        except OSError as exc:
            log.warning("could not read %s: %s", index_url, exc)
            failed = exc
            continue
        for published, url in candidates(index, day):
            try:
                page = net.get(url, headers=BROWSER).decode("utf-8", "replace")
            except OSError as exc:
                log.warning("could not read %s: %s", url, exc)
                failed = exc
                continue
            if parse_article(page):
                return url, published, page
    if failed is not None:
        raise OSError("could not read the Murcia radar list") from failed
    return None


def credit(url: str | None) -> str:
    """The attribution of a radar from the article at ``url``: the police's list, and
    the outlet it was read in."""
    host = urllib.parse.urlsplit(url or "").hostname or ""
    for domain, outlet in OUTLETS.items():
        if host == domain or host.endswith("." + domain):
            return CREDIT.format(outlet)
    return ATTRIBUTION


def to_radars(
    items: list[Announced],
    overpass_json: bytes,
    published: date,
    radius_m: float | Callable[[int | None], float],
    url: str | None,
    skipped: list[Announced] | None = None,
    geocoder: Geocoder | None = None,
    methods: dict[str, str] | None = None,
) -> list[Radar]:
    """Circles for every street placed on the map; the others go to ``skipped``,
    and how each placed one was found to ``methods``. ``radius_m`` is a number or
    a function of the street's limit in OSM. A street OSM misses is asked of
    ``geocoder``."""
    start, end = week_of(published)
    radars: list[Radar] = []
    for item, placed in place(items, overpass_json, radius_m, geocoder).items():
        centres = placed.centres
        if not centres:
            log.warning("could not place %s (%s) on the map; skipped", item.street, item.place)
            if skipped is not None:
                skipped.append(item)
            continue
        if methods is not None:
            methods[item.label()] = placed.method
        label = f"Radar anunciado {item.label()}"
        slug = re.sub(r"[^a-z0-9]+", "-", f"{item.street} {item.place or ''}".lower()).strip("-")
        for n, (lat, lon) in enumerate(centres):
            radars.append(
                Radar(
                    id=f"murcia-{start.isoformat()}-{slug}-{n}",
                    source="murcia",
                    kind="mobile_announced",
                    name=label,
                    lat=lat,
                    lon=lon,
                    radius_m=round(placed.radius_m),
                    valid_from=start,
                    valid_to=end,
                    url=url,
                    attribution=geometry_credit(credit(url), placed.method),
                    maxspeed=placed.maxspeed,
                    province=PROVINCE,
                )
            )
    return radars


def fetch(
    day: date, radius_m: float | Callable[[int | None], float] = 300
) -> tuple[list[Radar], WeeklyList]:
    """This week's radars ([] if the list is not published yet) and what the list gave."""
    status = WeeklyList("murcia", week_of(day)[0])
    found = find_article(day)
    if found is None:
        log.info("no Murcia radar list found for the week of %s", status.week)
        return [], status
    url, published, page = found
    items = parse_article(page)
    status.published, status.streets = published, items
    log.info("Murcia list %s: %d streets", url, len(items))
    # Same list, same queries: fetched once a week, not every hour.
    week = 7 * 86_400
    check = net.overpass_answer  # an error answer is never kept for the week
    query = places_query(items, BBOX)
    places = net.cached_get(OVERPASS, {"data": query}, max_age_s=week, validate=check)
    query = ways_query(items, places, BBOX)
    ways = net.cached_get(OVERPASS, {"data": query}, max_age_s=week, validate=check)
    overpass = json.dumps(
        {"elements": json.loads(places)["elements"] + json.loads(ways)["elements"]}
    ).encode()
    radars = to_radars(
        items, overpass, published, radius_m, url, status.skipped, Geocoder(TOWN), status.placed
    )
    return radars, status


def fetch_source(ctx: Context) -> SourceResult:
    radars, status = fetch(ctx.day, ctx.radius.street_m)
    return SourceResult(radars=radars, lists=[status])


SOURCE = Source(
    key="murcia",
    fetch=fetch_source,
    attribution=f"{ATTRIBUTION} y {CARTOCIUDAD_CREDIT}",
    licence=(
        "the Policía Local's weekly list, which it posts on its social networks as an "
        "image; the feed reads it in the press and takes its facts (street, district, "
        "week), not the article's text. Geometry: ODbL 1.0; a street OpenStreetMap misses, "
        "CartoCiudad (IGN), CC BY 4.0"
    ),
    max_age_s=7 * 86_400,
    provinces=frozenset({PROVINCE}),
)
