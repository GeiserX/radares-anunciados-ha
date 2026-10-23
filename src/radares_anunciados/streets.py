"""Turn "street, district" pairs from a police list into circles on the map.

Two Overpass queries: first each district (a place node or a district
boundary), then, within ``NEAR_M`` of each district, every way whose name ends
like its announced street. Names are then compared on a folded key, so the press's "Avenida
Juan de Borbón" matches OSM's "Avenida Don Juan de Borbón".

Common names repeat: Murcia has a dozen "Avenida Juan Carlos I" and a "Calle
Mayor" in most districts. So only the stretch of street near the district is
kept (a ring road announced "a su paso por El Puntal" is not the whole ring
road).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from .geo import cover, densify, distance_m
from .model import Announced, WeeklyList  # noqa: F401  (sources import them from here)

if TYPE_CHECKING:
    from .geocode import Geocoder

NEAR_M = 5000  # a street farther than this from its district is not it
CLIP_M = 1500  # keep the stretch up to this much farther than the nearest point

STREET_TYPES = {
    "avenida",
    "calle",
    "camino",
    "carretera",
    "carril",
    "paseo",
    "plaza",
    "ronda",
    "travesia",
    "vereda",
    "senda",
    "autovia",
    "costera",
    "glorieta",
    "rambla",
    "calzada",
    "pasaje",
    "callejon",
    "cuesta",
}
# Street types in the co-official languages, as the Spanish type. Catalan,
# Valencian and Galician write the type first ("Carrer Major", "Rúa Nova");
# Basque writes it last ("Miracruz kalea"). OSM often maps the Basque or Catalan
# name where a police list writes the Spanish one.
TYPE_FIRST = {
    "carrer": "calle",
    "avinguda": "avenida",
    "passeig": "paseo",
    "placa": "plaza",
    "cami": "camino",
    "travessera": "travesia",
    "travessia": "travesia",
    "rua": "calle",
    "praza": "plaza",
    "estrada": "carretera",
    "camino": "camino",
}
TYPE_LAST = {
    "kalea": "calle",
    "etorbidea": "avenida",
    "hiribidea": "avenida",
    "pasealekua": "paseo",
    "zumarkalea": "paseo",
    "plaza": "plaza",
    "enparantza": "plaza",
    "errepidea": "carretera",
    "bidea": "camino",
    "galtzada": "calzada",
}
# Words a list may drop or add without naming another street ("Mota del
# Reguerón" is "Mota Reguerón"; CartoCiudad writes "CALZADA ALDAPETA DE").
SMALL_WORDS = {"de", "del", "la", "las", "los", "el", "y", "d", "l", "don", "dona"}
_ROMAN = dict(
    zip(
        [str(n) for n in range(1, 31)],
        "i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx "
        "xxi xxii xxiii xxiv xxv xxvi xxvii xxviii xxix xxx".split(),
        strict=True,
    )
)
# A road number inside a street name: "Auxiliar MU-30", "Carretera Auxiliar A-30".
ROAD_NUMBER = re.compile(r"\b([A-Z]{1,3})\s?-\s?([A-Z]?\d{1,3})\b")
_LEADING = re.compile(r"^((de las|de los|de la|del|de|don|dona|la|las|los|el) )+")
_ACCENTS = {"a": "aá", "e": "eé", "i": "ií", "o": "oó", "u": "uúü", "n": "nñ"}


@dataclass(frozen=True)
class Placed:
    """An announced street on the map: circle centres, the speed limit OSM gives
    for most of the stretch (None if untagged), and the radius of the circles."""

    centres: list[tuple[float, float]]
    maxspeed: int | None
    radius_m: float
    # How it was found: "osm" (its name in OpenStreetMap), "osm_fuzzy" (a name
    # written another way), "osm_road" (the road its number names), "cartociudad"
    # or "nominatim" (a geocoder, ``geocode.py``); "" when not found.
    method: str = ""


def fold(text: str) -> str:
    """Lowercase, no accents, no punctuation, single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _kind(words: list[str]) -> tuple[str | None, list[str]]:
    """The street type, in Spanish, and the other words."""
    if words and words[0] in STREET_TYPES:
        return words[0], words[1:]
    if words and words[0] in TYPE_FIRST and len(words) > 1:
        return TYPE_FIRST[words[0]], words[1:]
    if len(words) > 1 and words[-1] in TYPE_LAST:
        return TYPE_LAST[words[-1]], words[:-1]
    return None, words


def street_key(name: str) -> tuple[str | None, str]:
    """('avenida', 'juan de borbon') for 'Avenida Don Juan de Borbón'; ('calle',
    'miracruz') for 'Miracruz kalea'."""
    kind, words = _kind(fold(name).split(" "))
    return kind, _LEADING.sub("", " ".join(words))


def loose_key(name: str) -> tuple[str | None, tuple[str, ...]]:
    """street_key without the small words anywhere and with numbers as roman
    numerals: 'Carril Molino del Batán' and 'Carril Molino Batán' are one key, and
    'Juan Carlos 1' is 'Juan Carlos I'."""
    kind, words = _kind(fold(name).split(" "))
    return kind, tuple(_ROMAN.get(w, w) for w in words if w and w not in SMALL_WORDS)


def similarity(written: str, mapped: str) -> float:
    """How alike two street names are, street type aside: 1.0 for the same loose
    key, else the share of characters in common (a typo: 'Loyoa' for 'Loyola').
    Names whose numbers differ ('Juan XXIII', 'Juan XXII') are never alike."""
    a, b = loose_key(written)[1], loose_key(mapped)[1]
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    numerals = set(_ROMAN.values())
    if {w for w in a if w in numerals} != {w for w in b if w in numerals}:
        return 0.0
    return SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()


_PLACE_STOPWORDS = {"y", "de", "del", "la", "las", "los", "el"}


def place_words(name: str) -> frozenset[str]:
    """{'santiago', 'zaraiche'} for both 'Santiago y Zaraiche' and 'Santiago Zaraiche'."""
    return frozenset(w for w in fold(name).split(" ") if w and w not in _PLACE_STOPWORDS)


def _anchors(place: str, places: dict[str, list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Points of the district. An exact name wins; otherwise every district
    whose name holds all the announced words ('Los Martinez' finds 'Los
    Martínez del Puerto')."""
    exact = places.get(fold(place))
    if exact:
        return exact
    wanted = place_words(place)
    return [
        p for name, pts in places.items() if wanted and wanted <= place_words(name) for p in pts
    ]


def _regex(text: str) -> str:
    out = ""
    for ch in fold(text):
        out += f"[{_ACCENTS[ch]}]" if ch in _ACCENTS else (ch if ch.isalnum() or ch == " " else ".")
    return out


_SEP = "[ '.-]+"
_SMALL_RE = "|".join(sorted(SMALL_WORDS - {"dona"}) + ["do[nñ]a"])
_LAST_RE = "|".join(sorted(TYPE_LAST))


def name_regex(street: str) -> str:
    """An Overpass regex for a mapped name ending like ``street``, with or
    without small words between its words ("Molino Batán" finds "Carril Molino
    del Batán"), and with a Basque street type after it ("Miracruz kalea")."""
    words = loose_key(street)[1]
    numerals = {v: k for k, v in _ROMAN.items()}
    parts = [f"({_regex(w)}|{numerals[w]})" if w in numerals else _regex(w) for w in words]
    between = f"{_SEP}(({_SMALL_RE}){_SEP})*"
    return between.join(parts) + f"({_SEP}({_LAST_RE}))?$"


def road_number(street: str) -> str | None:
    """'MU-30' for 'Auxiliar MU-30': a road named by its number."""
    m = ROAD_NUMBER.search(street)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def places_query(items: list[Announced], bbox: tuple[float, float, float, float]) -> str:
    """Step 1: every district named in the list (cheap: few elements carry place=*)."""
    south, west, north, east = bbox
    # Loose on purpose (the district's longest word, anywhere in the name):
    # the press writes "Santiago Zaraiche" for OSM's "Santiago y Zaraiche".
    # ``_anchors`` does the exact comparison afterwards.
    words = sorted(
        {max(place_words(i.place), key=len) for i in items if i.place and place_words(i.place)}
    )
    places = "".join(
        f'node["place"]["name"~"{_regex(w)}",i];'
        f'relation["boundary"="administrative"]["name"~"{_regex(w)}",i];'
        for w in words
    )
    return f"[out:json][timeout:90][bbox:{south},{west},{north},{east}];({places});out center;"


def ways_query(
    items: list[Announced], places_json: bytes, bbox: tuple[float, float, float, float]
) -> str:
    """Step 2: each street, searched only within ``NEAR_M`` of its district.

    A name regex over every way in the municipality is what makes Overpass
    time out (504); around a few points it answers in seconds.
    """
    south, west, north, east = bbox
    places = _places(json.loads(places_json).get("elements", []))
    parts = []
    for item in items:
        filters = [f'["name"~"{name_regex(item.street)}",i]'] if loose_key(item.street)[1] else []
        road = road_number(item.street)
        if road:  # "Auxiliar MU-30": the road it runs along
            filters.append(f'["ref"~"(^|;) *{road}( *;|$)"]')
        for f in filters:
            if not item.place:
                parts.append(f'way["highway"]{f}({south},{west},{north},{east});')
                continue
            for lat, lon in sorted(set(_anchors(item.place, places))):
                parts.append(f'way["highway"]{f}(around:{NEAR_M},{lat:.6f},{lon:.6f});')
    return f"[out:json][timeout:90];({''.join(parts)});out geom;"


def _places(elements: list[dict]) -> dict[str, list[tuple[float, float]]]:
    places: dict[str, list[tuple[float, float]]] = {}
    for el in elements:
        name = el.get("tags", {}).get("name", "")
        if el["type"] == "node":
            places.setdefault(fold(name), []).append((el["lat"], el["lon"]))
        elif el["type"] == "relation" and "center" in el:
            places.setdefault(fold(name), []).append((el["center"]["lat"], el["center"]["lon"]))
    return places


Line = list[tuple[float, float]]
# The OSM highway values of a road beside a numbered one: its service road, its slip roads.
_SIDE_ROAD = re.compile(r"service|unclassified|.+_link")
Way = tuple[Line, int | None]  # points and the way's maxspeed


def maxspeed_kmh(tag: str | None) -> int | None:
    """km/h from an OSM ``maxspeed`` value: "50" and "50 km/h" give 50; "30 mph",
    "ES:urban", "none" and "30;50" give None (not a single limit we can trust)."""
    if not tag:
        return None
    match = re.fullmatch(r"\s*(\d{1,3})\s*(km/h|kmh|kph)?\s*", tag)
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def _near(ways: list[Way], anchors: list[tuple[float, float]]) -> list[Way]:
    """The stretch of the street near the district, as runs of points.

    Keeps every point up to ``CLIP_M`` farther from the district than the
    street's nearest point. A same-named street in the next district may slip
    in; that costs an extra alert, while cutting a real stretch would cost a
    missed one.
    """
    if not ways or not anchors:
        return []

    def gap(p: tuple[float, float]) -> float:
        return min(distance_m(p, a) for a in anchors)

    dense = [(densify(line, 20.0), speed) for line, speed in ways]
    best = min(gap(p) for line, _ in dense for p in line)
    if best > NEAR_M:
        return []
    runs: list[Way] = []
    for line, speed in dense:
        run: Line = []
        for p in line:
            if gap(p) <= best + CLIP_M:
                run.append(p)
            elif run:
                runs.append((run, speed))
                run = []
        if run:
            runs.append((run, speed))
    return runs


def _limit(runs: list[Way]) -> int | None:
    """The limit on most of the stretch, by points (20 m apart once densified).
    A tie goes to the higher limit, which draws the larger circles."""
    weight: Counter[int] = Counter()
    for line, speed in runs:
        if speed:
            weight[speed] += len(line)
    if not weight:
        return None
    return max(weight, key=lambda s: (weight[s], s))


def place(
    items: list[Announced],
    overpass_json: bytes,
    radius_m: float | Callable[[int | None], float],
    geocoder: Geocoder | None = None,
) -> dict[Announced, Placed]:
    """Each announced street on the map. ``radius_m`` is a number, or a function
    of the street's limit (None when OSM has no single limit for it). A street or
    district not found gets no centres.

    The name as written first; then the same name written another way
    (``loose_key``); then the road a road number names; then, with a
    ``geocoder``, CartoCiudad and Nominatim. Each later step runs only for the
    streets the earlier ones missed."""
    elements = json.loads(overpass_json).get("elements", [])
    places = _places(elements)
    ways: dict[tuple[str | None, str], list[Way]] = {}
    loose: dict[tuple[str | None, tuple[str, ...]], list[Way]] = {}
    roads: dict[str, list[Way]] = {}
    side_roads: dict[str, list[Way]] = {}  # the service roads and slip roads of a road
    seen: set[int] = set()
    for el in elements:
        if el["type"] == "way" and "geometry" in el and el["id"] not in seen:
            seen.add(el["id"])  # two districts' searches can return the same way
            tags = el.get("tags", {})
            line = [(g["lat"], g["lon"]) for g in el["geometry"]]
            way = (line, maxspeed_kmh(tags.get("maxspeed")))
            ways.setdefault(street_key(tags.get("name", "")), []).append(way)
            loose.setdefault(loose_key(tags.get("name", "")), []).append(way)
            for ref in tags.get("ref", "").split(";"):
                if ref.strip():
                    roads.setdefault(ref.strip().replace(" ", ""), []).append(way)
                    if _SIDE_ROAD.fullmatch(tags.get("highway", "")):
                        side_roads.setdefault(ref.strip().replace(" ", ""), []).append(way)

    def runs_of(same_type: list[Way], any_type: list[Way], anchors) -> list[Way]:
        if anchors is None:
            return same_type or any_type
        # The press writes "Calle Campillo" for what is mapped as "Carril
        # Campillo": fall back to any street type only if none of the stated
        # type is near the district.
        return _near(same_type, anchors) or _near(any_type, anchors)

    result: dict[Announced, Placed] = {}
    for item in items:
        # A district we can't find means we can't tell which of the same-named
        # streets it is: better no circle than a dozen wrong ones. None: no district.
        anchors = _anchors(item.place, places) if item.place else None
        kind, core = street_key(item.street)
        same = [w for (k, c), g in ways.items() if c == core and k == kind for w in g]
        anyt = [w for (k, c), g in ways.items() if c == core for w in g]
        runs, method = runs_of(same, anyt, anchors), "osm"
        if not runs:
            lkind, lcore = loose_key(item.street)
            named = [(k, g) for (k, c), g in loose.items() if lcore and c == lcore]
            same = [w for k, g in named if k == lkind for w in g]
            anyt = [w for _, g in named for w in g]
            runs, method = runs_of(same, anyt, anchors), "osm_fuzzy"
        road = road_number(item.street)
        limit_known = True
        if not runs and road:
            # "Auxiliar MU-30" is the service road beside the MU-30: its own ways
            # when OSM tags them with the number, else the main road's, but then
            # without the main road's limit (120 would draw motorway-sized circles
            # for a radar on a 50 road): the circles cover the main road too.
            runs, method = runs_of(side_roads.get(road, []), [], anchors), "osm_road"
            if not runs:
                runs, limit_known = runs_of(roads.get(road, []), [], anchors), False
        if not runs and geocoder is not None:
            found = geocoder.street(item.street, item.place, anchors or [])
            if found:
                lines = [(line, None) for line in found.lines]
                runs, method = (_near(lines, anchors) if anchors else lines), found.method
        limit = _limit(runs) if limit_known else None
        radius = radius_m(limit) if callable(radius_m) else radius_m
        centres = cover([line for line, _ in runs], radius)
        result[item] = Placed(centres, limit, radius, method if centres else "")
    return result


def locate(
    items: list[Announced], overpass_json: bytes, radius_m: float
) -> dict[Announced, list[tuple[float, float]]]:
    """Circle centres per announced street. A street or district not found maps to []."""
    return {item: p.centres for item, p in place(items, overpass_json, radius_m).items()}
