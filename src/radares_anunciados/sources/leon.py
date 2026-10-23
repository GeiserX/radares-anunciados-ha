"""The mobile-radar schedule of the Ayuntamiento de León.

The council posts the whole month at once, around the 28th of the month
before: a table of day, shift (mañana/tarde), a cell with the shift's five
streets and a cell with their five speed limits, paired by position. We find
the post through the news blog's RSS, which carries the full body but keeps only
the last ~24 posts, so a post drops out within days: every post found is kept
in the cache folder for the rest of its month. Some months the post holds a PDF
instead of a table; then, and for any day no post covers, iLeón's weekly
article is the fallback (its monthly sitemap, slug "radares-moviles-leon", one
paragraph per "street - NN km/h" under day and shift headings).

Each street is valid on its own day only (the shift is ignored): one Radar per
street and day, with ``valid_from == valid_to``. The list gives no district, so
a street is placed by name alone, from one Overpass answer for ``BBOX`` that
also holds León's municipal border. Without a place, only the ways inside the
border count (the neighbours have their own Avenida de Madrid), and a name
still mapped at two places more than ``GAP_M`` apart, with no road number in
common, is skipped as ambiguous. A place in brackets ("LE-20 (Navatejera)",
"Prado Prior (Trobajo)") keeps the stretch near it instead; any other bracket
("INCIBE", "Prior. Peatonal") is a note and is dropped. The limit is the
published one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from html import unescape

from .. import net
from ..geocode import CARTOCIUDAD_CREDIT, SIMILAR, Geocoder, Town, geometry_credit
from ..model import Radar, SourceResult
from ..streetnames import expand, same_words, split
from ..streets import Announced, WeeklyList, fold, loose_key, place, place_words, similarity
from .base import PUBLIC_SECTOR_REUSE, Context, Source

log = logging.getLogger(__name__)

PROVINCE = "24"
# León municipality (OSM boundary 42.547-42.640, -5.631 to -5.534) plus about
# 1 km, which takes in Trobajo, Navatejera, Villaobispo and the LE-20 that the
# list names in brackets. (south, west, north, east)
BBOX = (42.54, -5.645, 42.65, -5.52)
GAP_M = 500  # ways of one name farther apart than this are two streets
TOWN = Town("León", "24089", BBOX)

RSS = (
    "https://aytoleon.es/es/actualidad/noticias/articulos/_layouts/15/listfeed.aspx"
    "?List=%7B4D573294-DC3C-4DAB-BFC8-0FEF9D1BCA7F%7D"
)
LEGAL_URL = "https://www.aytoleon.es/es/inicio/Paginas/terminos-y-condiciones-generales.aspx"
ILEON_SITEMAP = "https://ileon.eldiario.es/sitemap_contents_{year}_{month:02d}_961b5_001.xml"
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = (
    "[out:json][timeout:90][bbox:{},{},{},{}];"
    '(way["highway"]["name"];way["highway"]["ref"];node["place"]["name"];'
    'relation["boundary"="administrative"]["admin_level"="8"]["name"="León"];);out geom;'
).format(*BBOX)
BROWSER = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}

COUNCIL_ATTRIBUTION = (
    "Ayuntamiento de León, ubicación de los radares móviles; geometría © OpenStreetMap"
)
ILEON_ATTRIBUTION = (
    "Redacción ILEÓN, obtenido de ILEÓN (ileon.eldiario.es), CC BY-NC 4.0; "
    "geometría © OpenStreetMap"
)

# Names the list writes so that no rule finds them on the map: a typo, a word
# the map has and the list drops ("General", "de Veterinaria"), or the first
# word only ("Pº Condesa"). Keyed by the folded name after ``expand``; each value
# is the name in OpenStreetMap, checked against the ways inside ``BBOX`` on
# 2026-10-01.
ALIASES = {
    "avenida facultad": "Avenida de la Facultad de Veterinaria",
    "ingeniero saez de miera": "Avenida Ingeniero Sáenz de Miera",
    "san ignacio de loyoa": "Avenida San Ignacio de Loyola",
    "paseo condesa": "Paseo Condesa de Sagasta",
    "paseo de condesa": "Paseo Condesa de Sagasta",
    "gutierrez mellado": "Calle General Gutiérrez Mellado",
}

# Streets OSM maps in pieces more than ``GAP_M`` apart, with a differently named
# stretch between them, that are one street (checked in OSM on 2026-10-01).
ONE_STREET = {
    "Avenida de Asturias",  # the middle stretch is "Avenida de Asturias-Regimiento Almansa"
}

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_ROAD = re.compile(r"^[A-Z]{1,3}-\d{1,3}$")
# A street type abbreviation inside one paragraph: two streets typed as one
# ("San Ignacio de Loyola Avda. Magdalena").
_JOINED = re.compile(r"\s+(?=(?:Avda?\.|Av\.|P[º°]\.?\s|Po\.|Ctra\.|C/))")
_DAY = re.compile(
    r"^(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo),?\s+(\d{1,2})\s+de\s+(\w+)",
    re.I,
)
# "Fdez. Ladreda - 30 km/h"; a dash inside the name (LE-20) has no spaces around it
_ITEM = re.compile(r"^(?P<street>.+?)\s+[-–—]\s+(?P<limit>\d{1,3})\s*km\b", re.I)


@dataclass(frozen=True)
class Slot:
    """One line of a schedule: a street, as written, on one day."""

    day: date
    text: str  # "Fdez. Ladreda.", "LE-20 (Navatejera)"
    maxspeed: int | None


@dataclass(frozen=True)
class Schedule:
    """The slots one publication gave, and where it came from."""

    url: str
    published: date | None
    slots: list[Slot]
    attribution: str


def _text(fragment: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", "", fragment)).replace("\u200b", "")
    return re.sub(r"[\s\xa0]+", " ", text).strip()


def _paragraphs(cell: str) -> list[str]:
    parts = re.split(r"(?i)</p>|<br\s*/?>", cell)
    return [t for t in (_text(p) for p in parts) if t]


def _pair(streets: list[str], limits: list[int | None]) -> list[tuple[str, int | None]]:
    """Streets with their limits, by position. A cell with fewer streets than
    limits has two streets typed as one; we split them at the second's type.
    Counts that still differ keep a limit only if every limit is the same."""
    if len(streets) < len(limits):
        streets = [s for joined in streets for s in _JOINED.split(joined) if s]
    if len(streets) == len(limits):
        return list(zip(streets, limits, strict=True))
    same = set(limits)
    limit = same.pop() if len(same) == 1 else None
    log.warning(
        "León: %d streets and %d limits in one cell: %s", len(streets), len(limits), streets
    )
    return [(s, limit) for s in streets]


def parse_council(body: str, year: int, month: int) -> list[Slot]:
    """The slots of a council post's table. [] when the post has no table (a PDF)."""
    slots: list[Slot] = []
    day: int | None = None
    for row in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", body):
        cells = [_paragraphs(c) for c in re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", row)]
        if len(cells) < 4:
            continue
        first, _shift, streets, speeds = cells[:4]
        if first and re.fullmatch(r"\d{1,2}", first[0]):
            day = int(first[0])
        if day is None or not streets:
            continue
        limits = [int(m.group()) if (m := re.search(r"\d{1,3}", s)) else None for s in speeds]
        try:
            when = date(year, month, day)
        except ValueError:
            log.warning("León: no day %d in %d-%02d; row skipped", day, year, month)
            continue
        for text, limit in _pair(streets, limits):
            slots.append(Slot(when, text, limit))
    return slots


@dataclass(frozen=True)
class Post:
    """A council post found in the RSS: the month it covers and its body."""

    url: str
    published: date
    year: int
    month: int
    body: str


def council_posts(rss: str) -> list[Post]:
    """The radar posts in the council's RSS. Titles vary: "... informa de la
    ubicación de los radares móviles en el mes de octubre", "Ubicación de
    radares mes de julio"."""
    posts = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", rss):
        title = fold(_text(re.search(r"(?s)<title>(.*?)</title>", item).group(1)))
        named = re.search(r"\bradares\b.*\bmes de (\w+)", title)
        month = MONTHS.get(named.group(1)) if named else None
        link = re.search(r"(?s)<link>(.*?)</link>", item)
        body = re.search(r"(?s)<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", item)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", item)
        if month is None or not (link and body and pub):
            continue
        published = parsedate_to_datetime(pub.group(1)).date()
        # "durante el mes de octubre de 2026"; without a year, the next such month
        stated = re.search(rf"mes de {named.group(1)} de (\d{{4}})", fold(_text(body.group(1))))
        year = (
            int(stated.group(1))
            if stated
            else published.year + (1 if month < published.month else 0)
        )
        posts.append(Post(_text(link.group(1)), published, year, month, body.group(1)))
    return posts


def _post_path(year: int, month: int):
    return net.cache_dir() / "leon" / f"council-{year}-{month:02d}.json"


def remember_posts(posts: list[Post]) -> None:
    """Keep each post for its month: the RSS forgets it within days."""
    for p in posts:
        path = _post_path(p.year, p.month)
        data = {"url": p.url, "published": p.published.isoformat(), "body": p.body}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("could not keep the León post %s: %s", p.url, exc)


def remembered_post(year: int, month: int) -> Post | None:
    try:
        data = json.loads(_post_path(year, month).read_text("utf-8"))
        return Post(data["url"], date.fromisoformat(data["published"]), year, month, data["body"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError) as exc:
        log.warning("ignoring a damaged León post for %d-%02d: %s", year, month, exc)
        return None


def parse_ileon(page: str) -> tuple[date | None, list[Slot]]:
    """(publication date, slots) of an iLeón weekly article."""
    pub = re.search(r'"datePublished"\s*:\s*"(\d{4})-(\d\d)-(\d\d)', page)
    published = date(int(pub.group(1)), int(pub.group(2)), int(pub.group(3))) if pub else None
    slots: list[Slot] = []
    day: date | None = None
    for para in re.findall(r'(?s)<p class="article-text">(.*?)</p>', page):
        text = _text(para)
        heading = _DAY.match(text)
        if heading:
            month = MONTHS.get(fold(heading.group(2)))
            if month is None or published is None:
                day = None
                continue
            year = published.year
            if month == 1 and published.month == 12:
                year += 1
            elif month == 12 and published.month == 1:
                year -= 1
            try:
                day = date(year, month, int(heading.group(1)))
            except ValueError:
                day = None
            continue
        item = _ITEM.match(text)
        if item and day is not None:
            slots.append(Slot(day, item.group("street"), int(item.group("limit"))))
    return published, slots


def ileon_candidates(sitemap: str) -> list[str]:
    """Radar articles in an iLeón sitemap, newest first (by content id)."""
    urls = set(re.findall(r"<loc>\s*([^<\s]*/radares-moviles-leon[^<\s]*)\s*</loc>", sitemap))

    def content_id(url: str) -> int:
        m = re.search(r"_1_(\d+)\.html", url)
        return int(m.group(1)) if m else 0

    return sorted(urls, key=content_id, reverse=True)


# ---- the map --------------------------------------------------------------


def _box(line: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lats = [p[0] for p in line]
    lons = [p[1] for p in line]
    return min(lats), min(lons), max(lats), max(lons)


def _refs(way: dict) -> set[str]:
    return {r.strip() for r in way.get("tags", {}).get("ref", "").split(";") if r.strip()}


def _groups(ways: list[dict]) -> int:
    """How many places, ``GAP_M`` apart or more, the ways lie in (by bounding box).
    Ways with a road number in common are one road, however far apart."""
    boxes = [_box([(g["lat"], g["lon"]) for g in w["geometry"]]) for w in ways]
    refs = [_refs(w) for w in ways]
    dlat = GAP_M / 111_320
    dlon = GAP_M / (111_320 * math.cos(math.radians(BBOX[0])))
    parent = list(range(len(boxes)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(boxes):
        for j in range(i):
            b = boxes[j]
            if refs[i] & refs[j] or (
                a[0] - dlat <= b[2]
                and b[0] - dlat <= a[2]
                and a[1] - dlon <= b[3]
                and b[1] - dlon <= a[3]
            ):
                parent[root(i)] = root(j)
    return len({root(i) for i in range(len(boxes))})


@dataclass(frozen=True)
class Found:
    """A list's street on the map, or why not."""

    item: Announced  # the name drivers read: OSM's name, or the road number
    ways: tuple[dict, ...] = ()
    reason: str = ""  # why it was skipped
    method: str = "osm"  # how it was found (``streets.Placed.method``)


class StreetMap:
    """Every named or numbered road and every named place inside ``BBOX``."""

    def __init__(self, overpass_json: bytes):
        data = json.loads(overpass_json)
        remark = data.get("remark", "")
        if "error" in remark.lower():
            raise OSError(f"Overpass answered with an error: {remark}")
        elements = data.get("elements", [])
        self.places = [e for e in elements if e["type"] == "node"]
        self.ways = [e for e in elements if e["type"] == "way" and e.get("geometry")]
        self._names = [(w, w.get("tags", {}).get("name", "")) for w in self.ways]
        # The municipality's border, as segments: the even-odd test needs no ring order.
        self._border = [
            ((a["lat"], a["lon"]), (b["lat"], b["lon"]))
            for e in elements
            if e["type"] == "relation"
            for m in e.get("members", [])
            if m.get("role") in ("outer", "inner") and m.get("geometry")
            for a, b in zip(m["geometry"], m["geometry"][1:], strict=False)
            if a and b
        ]
        if not self._border:
            raise OSError("the map of León came without the municipality's border")

    def _inside(self, lat: float, lon: float) -> bool:
        inside = False
        for (y1, x1), (y2, x2) in self._border:
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside

    def in_town(self, way: dict) -> bool:
        """Whether any point of the way lies inside the municipality of León."""
        return any(self._inside(g["lat"], g["lon"]) for g in way["geometry"])

    def is_place(self, text: str) -> bool:
        wanted = place_words(text)
        return bool(wanted) and any(
            wanted <= place_words(p.get("tags", {}).get("name", "")) for p in self.places
        )

    def announced(self, text: str) -> Announced:
        """'Fdez. Ladreda.' -> Fernández Ladreda; 'LE-20 (Navatejera)' -> LE-20 in
        Navatejera. A bracket that names no mapped place ('INCIBE', 'Prior.
        Peatonal') is a note and goes."""
        note = re.search(r"\(([^)]*)\)", text)
        street = expand(re.sub(r"\([^)]*\)?", " ", text))
        street = ALIASES.get(fold(street), street)
        where = note.group(1).strip() if note else ""
        return Announced(street, where if where and self.is_place(where) else None)

    def _fuzzy(self, street: str) -> list[dict]:
        """The ways of the one in-town name most like ``street`` (a typo, a word
        dropped), when that name is alike enough and no other name ties it."""
        scores: dict[str, float] = {}
        for _, name in self._names:
            if name and name not in scores:
                scores[name] = similarity(street, name)
        best = max(scores.values(), default=0.0)
        top = {n for n, sc in scores.items() if sc == best}
        # "Avenida Padre Isla" and "Avenida del Padre Isla" are one street; two others tie
        if best < SIMILAR or len({loose_key(n)[1] for n in top}) > 1:
            return []
        return [w for w, name in self._names if name in top and self.in_town(w)]

    def find(self, item: Announced, geocoder: Geocoder | None = None) -> Found:
        road = bool(_ROAD.match(item.street))
        method = "osm"
        if road:
            ways = [w for w in self.ways if item.street in _refs(w)]
        else:
            kind, words = split(item.street)
            ways = [w for w, name in self._names if name and same_words(words, split(name)[1])]
            if kind:  # "Avda. Madrid": not Carretera Madrid, if an avenue of that name exists
                ways = [w for w in ways if split(w["tags"]["name"])[0] == kind] or ways
            if not ways and item.place is None:
                ways, method = self._fuzzy(item.street), "osm_fuzzy"
            if not ways and item.place is None and geocoder is not None:
                found = geocoder.street(item.street, None, [])
                if found:
                    method = found.method
                    ways = [  # as OSM ways; negative ids, which OSM never uses
                        {
                            "type": "way",
                            "id": -n,
                            # the list's spelling: CartoCiudad writes "CALLE X"
                            "tags": {"name": item.street},
                            "geometry": [{"lat": a, "lon": b} for a, b in line],
                        }
                        for n, line in enumerate(found.lines, 1)
                    ]
        reason = "not on the map"
        if ways and item.place is None:
            # No place given: the street is the council's, inside its municipality.
            ways = [w for w in ways if self.in_town(w)]
            reason = "not in the municipality of León"
        names = Counter(w["tags"]["name"] for w in ways if not road)
        label = max(names, key=lambda n: (names[n], n)) if names else item.street
        found = Announced(label, item.place)
        if not ways:
            return Found(found, reason=reason)
        if item.place is None and label not in ONE_STREET and _groups(ways) > 1:
            return Found(found, reason="name mapped at several places")
        return Found(found, tuple(ways), method=method)

    def centres(self, found: Found, radius_m: float) -> list[tuple[float, float]]:
        """Circles along the found ways (near the bracketed place, if any)."""
        name = found.item.street
        elements = list(self.places) + [
            {**w, "tags": {**w.get("tags", {}), "name": name}} for w in found.ways
        ]
        payload = json.dumps({"elements": elements}).encode()
        return place([found.item], payload, radius_m)[found.item].centres


# ---- fetching -------------------------------------------------------------


def week_of(day: date) -> list[date]:
    monday = day - timedelta(days=day.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


def _get(url: str, max_age_s: int) -> str:
    return net.cached_get(url, headers=BROWSER, max_age_s=max_age_s).decode("utf-8", "replace")


def schedules(day: date, max_age_s: int) -> tuple[list[Schedule], list[OSError]]:
    """The council's posts for the months of this week, and iLeón's article for
    the days of this week no post covers. Downloads that failed come back too."""
    week = week_of(day)
    failed: list[OSError] = []
    try:
        remember_posts(council_posts(_get(RSS, max_age_s)))
    except OSError as exc:
        log.warning("could not read the León council RSS: %s", exc)
        failed.append(exc)
    found: list[Schedule] = []
    covered: set[date] = set()
    for year, month in sorted({(d.year, d.month) for d in week}):
        post = remembered_post(year, month)
        if post is None:
            continue
        slots = parse_council(post.body, year, month)
        if not slots:
            log.warning("León post for %d-%02d has no table (a PDF?): %s", year, month, post.url)
            continue
        found.append(Schedule(post.url, post.published, slots, COUNCIL_ATTRIBUTION))
        covered |= {s.day for s in slots}
    missing = [d for d in week if d not in covered]
    if missing:
        article = _ileon(week, max_age_s, failed)
        if article:
            slots = [s for s in article.slots if s.day in missing]
            found.append(Schedule(article.url, article.published, slots, ILEON_ATTRIBUTION))
    return found, failed


def _ileon(week: list[date], max_age_s: int, failed: list[OSError]) -> Schedule | None:
    """iLeón's article for this week: published on its Monday, or the day before."""
    months = sorted({(d.year, d.month) for d in (week[0] - timedelta(days=1), week[0])})
    urls: list[str] = []
    for year, month in reversed(months):
        try:
            urls += ileon_candidates(_get(ILEON_SITEMAP.format(year=year, month=month), max_age_s))
        except OSError as exc:
            log.warning("could not read the iLeón sitemap %d-%02d: %s", year, month, exc)
            failed.append(exc)
    for url in urls[:3]:
        try:
            published, slots = parse_ileon(_get(url, 86_400))
        except OSError as exc:
            log.warning("could not read %s: %s", url, exc)
            failed.append(exc)
            continue
        if any(s.day in week for s in slots):
            return Schedule(url, published, slots, ILEON_ATTRIBUTION)
    return None


def to_radars(
    found: list[Schedule],
    street_map: StreetMap,
    radius_m: Callable[[int | None], float],
    week: list[date],
    geocoder: Geocoder | None = None,
) -> tuple[list[Radar], WeeklyList]:
    """One set of circles per street and day. ``radius_m`` is a function of the
    published limit. The list reports this week's streets, how each was placed,
    and those skipped. A street not on the map is asked of ``geocoder``."""
    finds: dict[str, Found] = {}
    # One street twice on a day (both shifts, or written two ways): one set of
    # circles, with the higher limit.
    best: dict[tuple[date, Announced], tuple[Found, int | None, Schedule]] = {}
    for schedule in found:
        for slot in schedule.slots:
            if slot.text not in finds:
                finds[slot.text] = street_map.find(street_map.announced(slot.text), geocoder)
            f = finds[slot.text]
            key = (slot.day, f.item)
            if key not in best or (slot.maxspeed or 0) > (best[key][1] or 0):
                best[key] = (f, slot.maxspeed, schedule)

    status = WeeklyList("leon", week[0])
    circles: dict[tuple[Announced, float], list[tuple[float, float]]] = {}
    radars: list[Radar] = []
    for (day, street), (f, limit, schedule) in sorted(
        best.items(), key=lambda kv: (kv[0][0], kv[0][1].label())
    ):
        if day in week:
            if street not in status.streets:
                status.streets.append(street)
            if schedule.published and (status.published or date.min) < schedule.published:
                status.published = schedule.published
        radius = radius_m(limit)
        centres: list[tuple[float, float]] = []
        if f.ways:
            if (street, radius) not in circles:
                circles[street, radius] = street_map.centres(f, radius)
            centres = circles[street, radius]
        if not centres:
            if day in week and street not in status.skipped:
                why = f.reason or f"not near {street.place}"
                log.warning("León: could not place %s (%s); skipped", street.label(), why)
                status.skipped.append(street)
            continue
        if day in week:
            status.placed[street.label()] = f.method
        slug = re.sub(r"[^a-z0-9]+", "-", fold(street.label())).strip("-")
        for n, (lat, lon) in enumerate(centres):
            radars.append(
                Radar(
                    id=f"leon-{day.isoformat()}-{slug}-{n}",
                    source="leon",
                    kind="mobile_announced",
                    name=f"Radar anunciado {street.label()}",
                    lat=lat,
                    lon=lon,
                    radius_m=round(radius),
                    valid_from=day,
                    valid_to=day,
                    url=schedule.url,
                    attribution=geometry_credit(schedule.attribution, f.method),
                    maxspeed=limit,
                    province=PROVINCE,
                )
            )
    return radars, status


def load_street_map(max_age_s: int = 30 * 86_400) -> StreetMap:
    """The map changes slowly: one download a month. Unlike ``net.cached_get``, an
    answer is cached only once it reads as a map: Overpass answers 200 with an
    error remark when it is busy, and a cached bad copy would keep León down
    until the month is over."""
    query = hashlib.sha256(OVERPASS_QUERY.encode()).hexdigest()[:16]
    path = net.cache_dir() / f"leon-overpass-{query}.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        try:
            return StreetMap(path.read_bytes())
        except (OSError, ValueError) as exc:
            log.warning("the cached map of León is unreadable (%s); downloading it again", exc)
    body = net.get(OVERPASS, {"data": OVERPASS_QUERY})
    try:
        street_map = StreetMap(body)
    except ValueError as exc:  # not JSON: a proxy's error page
        raise OSError(f"Overpass did not answer with a map: {exc}") from exc
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
    except OSError as exc:  # an unwritable cache must not fail a download that worked
        log.warning("could not cache the map of León in %s: %s", path.parent, exc)
    return street_map


def fetch(ctx: Context) -> SourceResult:
    week = week_of(ctx.day)
    found, failed = schedules(ctx.day, ctx.max_age_s)
    covered = {s.day for f in found for s in f.slots}
    if failed and any(d >= ctx.day and d not in covered for d in week):
        # an empty or partial week ahead would delete zones; a failed run keeps the
        # last good ones. Past days no schedule covers no longer matter.
        raise OSError(f"could not read the León radar schedule: {failed[0]}") from failed[0]
    if not found:
        log.info("no León radar schedule found for the week of %s", week[0])
        return SourceResult(lists=[WeeklyList("leon", week[0])])
    street_map = load_street_map()
    radars, status = to_radars(found, street_map, ctx.radius.street_m, week, Geocoder(TOWN))
    return SourceResult(radars=radars, lists=[status])


SOURCE = Source(
    key="leon",
    fetch=fetch,
    attribution=(
        "Ayuntamiento de León (ubicación mensual de los radares móviles); "
        "Redacción ILEÓN, obtenido de ILEÓN (ileon.eldiario.es); geometría © OpenStreetMap "
        f"y {CARTOCIUDAD_CREDIT}"
    ),
    licence=(
        f"Ayuntamiento de León's posts: {PUBLIC_SECTOR_REUSE}; its portal's terms reserve "
        f"reproduction except for personal and private use ({LEGAL_URL}). Days only iLeón "
        "covers: the facts iLeón reports (street, day, limit), not its text, which is "
        "CC BY-NC 4.0. Geometry: ODbL 1.0; a street OpenStreetMap misses, CartoCiudad "
        "(IGN), CC BY 4.0"
    ),
    spanish_ip_hosts=frozenset({"aytoleon.es"}),  # the council; iLeón answers anywhere
    max_age_s=6 * 3600,
    provinces=frozenset({PROVINCE}),
)
