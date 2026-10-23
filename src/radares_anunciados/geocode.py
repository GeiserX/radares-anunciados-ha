"""Find an announced street the map search missed, through public geocoders.

``streets.place`` and the León and Donostia sources first look a street up in
OpenStreetMap through Overpass. When that finds nothing, a street still has a
real place: the list names it because a radar stands there. This module asks,
in order:

1. CartoCiudad (IGN, Spain's official street register, CC BY 4.0). Its
   ``candidates`` call finds the street by name inside the municipality; its
   ``find`` call gives the street's whole line.
2. Nominatim (OpenStreetMap's own search, ODbL 1.0), a structured search for
   the street in the town, with the way's line. Its usage policy allows at
   most one request a second with a User-Agent that names the program, and
   asks that answers be cached: both are done here.

An answer counts only if it is a street of that municipality whose name is the
announced one or close to it (``streets.similarity``), and, when the district
is on the map, lies within ``streets.NEAR_M`` of it. A district not on the map
must then appear in the answer's own address. Several streets of that name
and no district to choose between them gives no answer: a wrong street costs
an alert in the wrong place.

Every answer, an empty one too, is kept in ``geocoded/<INE code>.json`` in the
cache folder, so a street is asked once (an empty answer is asked again after
``EMPTY_TTL_S``). A run asks at most ``BUDGET`` requests; a geocoder that is
down or out of budget leaves the street unplaced for this run, never fails it.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import net
from .geo import distance_m
from .streets import NEAR_M, Line, fold, place_words, similarity

log = logging.getLogger(__name__)

CARTOCIUDAD = "https://www.cartociudad.es/geocoder/api/geocoder"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
CARTOCIUDAD_CREDIT = "CartoCiudad, CC BY 4.0 scne.es (© Instituto Geográfico Nacional)"
OSM_CREDIT = "© OpenStreetMap"
SIMILAR = 0.85  # streets.similarity at or above this is the same street
BUDGET = 60  # requests per run and town
TTL_S = 180 * 86_400  # a street found stays found this long
EMPTY_TTL_S = 14 * 86_400  # a street not found is asked again after this
_WAIT_S = {"nominatim": 1.1, "cartociudad": 0.2}  # between two requests to one geocoder
_last: dict[str, float] = {}  # the time of the last request to each geocoder
# The record types CartoCiudad should not answer with: only streets ("callejero")
# and roads ("carretera") are wanted.
_NOT_STREETS = (
    "portal,toponimo,municipio,provincia,comunidad autonoma,poblacion,codpost,"
    "expendeduria,ngbe,punto_recarga_electrica,refcatastral"
)


@dataclass(frozen=True)
class Town:
    """The municipality a list's streets are in."""

    name: str  # as CartoCiudad writes it: "Murcia", "Donostia/San Sebastián"
    ine: str  # five-digit INE code, "30030"
    bbox: tuple[float, float, float, float]  # (south, west, north, east)


@dataclass(frozen=True)
class Found:
    """A street a geocoder found: its line, and who found it."""

    lines: list[Line]
    method: str  # "cartociudad" or "nominatim"
    name: str  # the street's name in that geocoder


@dataclass
class Geocoder:
    """Looks streets up in one town. Make one per source run."""

    town: Town
    budget: int = BUDGET
    _cache: dict[str, Any] | None = field(default=None, repr=False)

    # ---- the cache ------------------------------------------------------

    def _path(self):
        return net.cache_dir() / "geocoded" / f"{self.town.ine}.json"

    def _load(self) -> dict[str, Any]:
        if self._cache is None:
            try:
                self._cache = json.loads(self._path().read_text("utf-8"))
            except FileNotFoundError:
                self._cache = {}
            except (OSError, ValueError) as exc:
                log.warning("ignoring a damaged geocoder cache %s: %s", self._path(), exc)
                self._cache = {}
        return self._cache

    def _save(self) -> None:
        path = self._path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._load(), ensure_ascii=False, sort_keys=True), "utf-8")
            tmp.replace(path)
        except OSError as exc:  # a cache that can't be written costs a request next run
            log.warning("could not keep the geocoder answers in %s: %s", path, exc)

    def _candidates(self, provider: str, street: str, district: str | None) -> list[dict] | None:
        """The provider's streets for this name: from the cache, or asked now.
        None: not known this run (down, or out of budget)."""
        key = f"{provider}|{street}|{district or ''}"
        cache = self._load()
        kept = cache.get(key)
        try:
            ttl = TTL_S if kept["candidates"] else EMPTY_TTL_S
            if time.time() - kept["at"] < ttl:
                return kept["candidates"]
        except (KeyError, TypeError):  # not kept, or kept in another shape: ask again
            pass
        ask = _cartociudad if provider == "cartociudad" else _nominatim
        try:
            candidates = ask(self, street, district)
        except _OutOfBudget:
            log.warning("geocoder budget for %s spent; %s stays unplaced", self.town.name, street)
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("%s could not look up %s: %s", provider, street, exc)
            return None
        cache[key] = {"at": time.time(), "candidates": candidates}
        self._save()
        return candidates

    def _get(self, provider: str, url: str, params: dict[str, str]) -> Any:
        if self.budget <= 0:
            raise _OutOfBudget
        self.budget -= 1
        _wait(provider)
        try:
            query = urllib.parse.urlencode(params)
            return json.loads(net.get(f"{url}?{query}", timeout=30, tries=2))
        finally:
            _last[provider] = time.monotonic()

    # ---- choosing -------------------------------------------------------

    def street(
        self, street: str, district: str | None, anchors: list[tuple[float, float]]
    ) -> Found | None:
        """The announced street's line, or None. ``anchors`` are the district's
        points on the map ([] when it has no district or it is not on the map)."""
        for provider in ("cartociudad", "nominatim"):
            candidates = self._candidates(provider, street, district)
            if candidates:
                found = choose(street, district, anchors, candidates)
                if found:
                    return Found(found["lines"], provider, found["name"])
        return None


class _OutOfBudget(Exception):
    pass


def _wait(provider: str) -> None:
    """Sleep until ``provider`` may be asked again (``_WAIT_S`` after the last request)."""
    wait = _last.get(provider, float("-inf")) + _WAIT_S[provider] - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _gap(lines: list[Line], anchors: list[tuple[float, float]]) -> float:
    return min((distance_m(p, a) for line in lines for p in line for a in anchors), default=1e12)


def choose(
    street: str,
    district: str | None,
    anchors: list[tuple[float, float]],
    candidates: list[dict],
) -> dict | None:
    """The candidate that is the announced street, or None.

    A candidate is ``{"name", "where", "lines"}``: the street's name, its full
    address (district, town) and its line as (lat, lon) points.
    """
    named = [
        (similarity(street, c["name"]), c) for c in candidates if c.get("lines") and any(c["lines"])
    ]
    named = [(s, c) for s, c in named if s >= SIMILAR]
    if not named:
        return None
    if anchors:
        near = [(s, _gap(c["lines"], anchors), c) for s, c in named]
        near = [n for n in near if n[1] <= NEAR_M]
        # the closest name first, then the closest street
        return min(near, key=lambda n: (-n[0], n[1]))[2] if near else None
    if district:
        wanted = place_words(district)
        named = [(s, c) for s, c in named if wanted <= set(fold(c.get("where", "")).split())]
        if not named:
            return None
    best = max(s for s, _ in named)
    top = [c for s, c in named if s == best]
    names = {fold(c["name"]) for c in top}
    if len(top) > 1 and not _together([c["lines"] for c in top]):
        log.info("%s: %d streets of that name and nothing to choose by", street, len(names))
        return None
    return {"name": top[0]["name"], "lines": [line for c in top for line in c["lines"]]}


def _together(groups: list[list[Line]], gap_m: float = 500) -> bool:
    """Whether every group of lines lies within ``gap_m`` of the first: pieces
    of one street, not two streets of one name."""
    first = [p for line in groups[0] for p in line]
    return all(
        min(distance_m(p, q) for line in g for p in line for q in first) <= gap_m
        for g in groups[1:]
    )


# ---- CartoCiudad ----------------------------------------------------------

_WKT_LINE = re.compile(r"\(([-\d. ,]+)\)")


def wkt_lines(wkt: str | None) -> list[Line]:
    """(lat, lon) lines of a WKT POINT, LINESTRING or MULTILINESTRING."""
    lines: list[Line] = []
    for body in _WKT_LINE.findall(wkt or ""):
        line = []
        for pair in body.split(","):
            lon, lat = (float(v) for v in pair.split())
            line.append((lat, lon))
        if line:
            lines.append(line)
    return lines


def _street_part(address: str) -> str:
    """'CALLE MARBELLA (ESPARRAGAL), Villa de la Plata (Murcia)' -> 'CALLE MARBELLA'."""
    return re.split(r"\s*[(,]", address, maxsplit=1)[0].strip()


def _cartociudad(geocoder: Geocoder, street: str, district: str | None) -> list[dict]:
    """Streets of that name in the town and, asked apart, in the district
    (``poblacion_filter``): a common name ("Calle Mayor") has more namesakes in
    the town than one answer lists."""
    town = geocoder.town
    base = {"q": street, "municipio_filter": town.name, "no_process": _NOT_STREETS, "limit": "10"}
    asks = ([{**base, "poblacion_filter": district}] if district else []) + [base]
    found: dict[str, dict] = {}
    for params in asks:
        for c in geocoder._get("cartociudad", f"{CARTOCIUDAD}/candidates", params) or []:
            if c.get("muniCode") == town.ine and c.get("type") in ("callejero", "carretera"):
                found.setdefault(str(c["id"]), c)
    wanted = place_words(district) if district else frozenset()

    def rank(c: dict) -> tuple[float, bool]:
        where = set(fold(f"{c['address']} {c.get('poblacion') or ''}").split())
        return similarity(street, _street_part(c["address"])), bool(wanted) and wanted <= where

    # Only the likely ones get their line asked: one request each.
    likely = sorted((c for c in found.values() if rank(c)[0] >= SIMILAR), key=rank, reverse=True)
    out = []
    for c in likely[:4]:
        full = geocoder._get(
            "cartociudad", f"{CARTOCIUDAD}/find", {"id": str(c["id"]), "type": c["type"]}
        )
        out.append(
            {
                "name": _street_part(c["address"]),
                "where": f"{c['address']} {c.get('poblacion') or ''}",
                "lines": wkt_lines((full or {}).get("geom")),
            }
        )
    return out


# ---- Nominatim ------------------------------------------------------------


def geojson_lines(geometry: dict | None) -> list[Line]:
    """(lat, lon) lines of a GeoJSON Point, LineString or MultiLineString."""
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates") or []
    if kind == "Point":
        parts = [[coords]]
    elif kind == "LineString":
        parts = [coords]
    elif kind == "MultiLineString":
        parts = coords
    else:
        return []
    return [[(float(p[1]), float(p[0])) for p in part] for part in parts if part]


def _nominatim(geocoder: Geocoder, street: str, district: str | None) -> list[dict]:
    south, west, north, east = geocoder.town.bbox
    answer = geocoder._get(
        "nominatim",
        NOMINATIM,
        {
            "street": street,
            "city": geocoder.town.name,
            "countrycodes": "es",
            "format": "jsonv2",
            "polygon_geojson": "1",
            "viewbox": f"{west},{north},{east},{south}",
            "bounded": "1",
            "limit": "10",
        },
    )
    return [
        {
            "name": r.get("name") or "",
            "where": r.get("display_name") or "",
            "lines": geojson_lines(r.get("geojson")),
        }
        for r in answer or []
        if r.get("category") == "highway"
    ]


def geometry_credit(attribution: str, method: str) -> str:
    """A radar's attribution with the credit of whoever gave its line. Lists
    credit OpenStreetMap ("geometría © OpenStreetMap") by default."""
    if method != "cartociudad":
        if method == "nominatim" and OSM_CREDIT not in attribution:
            return f"{attribution}; geometría {OSM_CREDIT}"
        return attribution
    osm = f"geometría {OSM_CREDIT}"
    if osm in attribution:
        return attribution.replace(osm, f"geometría {CARTOCIUDAD_CREDIT}")
    return f"{attribution}; geometría {CARTOCIUDAD_CREDIT}"
