"""Fixed radars of the Basque Government (Trafikoa, Dirección de Tráfico).

The page "Cabinas de radar fijo" draws every booth on a map from inline
JavaScript, one block per radar: ETRS89 UTM 30N coordinates (EPSG:25830), the
territory, and a popup with name, direction, municipality, territory, road, km
and a free-text speed limit ("80 km/h", "80 Km/h", "80", "60/80 km/h", "-").
Each block repeats its popup for the Basque-language page; the first one is the
Spanish text. The site answers only from a Spanish IP.

The Basque Government publishes no reuse licence for it, and the euskadi.eus
legal notice reserves the site's content. The feed reuses the data under Ley
37/2007 on the reuse of public-sector information (LICENSE-DATA.md).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

from .. import net
from ..geo import utm_to_wgs84
from ..model import Radar, SourceResult
from .base import PUBLIC_SECTOR_REUSE, Context, Source

log = logging.getLogger(__name__)

URL = "https://apps.trafikoa.euskadi.eus/lfr/web/trafikoa/cabinas-de-radar-fijo"
LEGAL_URL = "https://www.euskadi.eus/informacion/-/informacion-legal"
ATTRIBUTION = "Gobierno Vasco / Eusko Jaurlaritza, Dirección de Tráfico (Trafikoa)"
LICENCE = f"{PUBLIC_SECTOR_REUSE}. Its legal notice reserves the site's content: {LEGAL_URL}"
PROVINCES = {"Araba": "01", "Gipuzkoa": "20", "Bizkaia": "48"}
UTM_ZONE = 30  # the page draws with wkid ETRS89, coordType UTM

_BLOCK = re.compile(
    r'var x = (?P<x>-?[\d.]+);\s*var y = (?P<y>-?[\d.]+);\s*var th = "(?P<th>[^"]*)";'
    r'\s*var popupTitle = "(?P<title>[^"]*)";.*?var popupValores = (?P<values>\[.*?\]);',
    re.S,
)
# A red-light camera is listed with the booths; it controls no speed.
_RED_LIGHT = re.compile(r"FOTO.?ROJO", re.IGNORECASE)


def speed_kmh(text: str) -> int | None:
    """'80 km/h' -> 80; '60/80 km/h' (a variable limit) -> 80, so the zone is
    sized for the faster one; '-' -> None."""
    found = [int(n) for n in re.findall(r"\d+", text) if 10 <= int(n) <= 130]
    return max(found) if found else None


def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def parse(page: str, provinces: frozenset[str] | set[str] | None = None) -> list[Radar]:
    """Every booth of the page in ``provinces`` (None: all three)."""
    radars: list[Radar] = []
    ids: set[str] = set()
    for m in _BLOCK.finditer(page):
        try:
            values = json.loads(m.group("values"))
            name, direction, _town, territory, road, km, limit = (
                str(v).strip() for v in values[:7]
            )
        except ValueError:
            log.warning("euskadi: unreadable radar block %r; skipped", m.group("title"))
            continue
        if _RED_LIGHT.search(name):
            continue
        province = PROVINCES.get(m.group("th")) or PROVINCES.get(territory)
        if province is None:
            log.warning("euskadi: radar %r in unknown territory %r; skipped", name, territory)
            continue
        if provinces is not None and province not in provinces:
            continue
        lat, lon = utm_to_wgs84(float(m.group("x")), float(m.group("y")), UTM_ZONE)
        label = road
        try:
            label += f" km {float(km):.1f}"
        except ValueError:
            pass
        if direction:
            label += f" (sentido {direction})"
        section = "TRAMO" in name.upper()
        rid = f"euskadi-{_slug(name)}"
        n = 2
        while rid in ids:  # names are unique today; keep ids unique if one repeats
            rid = f"euskadi-{_slug(name)}-{n}"
            n += 1
        ids.add(rid)
        radars.append(
            Radar(
                id=rid,
                source="euskadi",
                kind="section" if section else "fixed",
                name=f"Radar {'de tramo' if section else 'fijo'} {label}",
                lat=lat,
                lon=lon,
                radius_m=500,
                url=URL,
                attribution=ATTRIBUTION,
                maxspeed=speed_kmh(limit),
                direction=direction or None,
                province=province,
            )
        )
    return radars


def fetch(ctx: Context) -> SourceResult:
    page = net.cached_get(URL, max_age_s=ctx.max_age_s).decode("utf-8", "replace")
    # Read every territory first: an empty answer would delete every zone, so a
    # page with no block, or with blocks none of which reads, must fail instead.
    radars = parse(page)
    if not radars:
        raise ValueError("euskadi: no readable radar block on the page; did its layout change?")
    if ctx.provinces is not None:
        radars = [r for r in radars if r.province in ctx.provinces]
    return SourceResult(radars=radars)


SOURCE = Source(
    key="euskadi",
    cameras=True,
    fetch=fetch,
    attribution=ATTRIBUTION,
    licence=LICENCE,
    spanish_ip_hosts=frozenset({"euskadi.eus"}),
    max_age_s=86_400,
    provinces=frozenset(PROVINCES.values()),
)
