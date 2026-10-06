"""Fixed radars of the Government of Navarra, from its traffic viewer.

The viewer (visorcontroltrafico.navarra.es) asks its API for the radars with a
``POST api/openits/elements/radars``: one element per radar, named like
"ALSASUA - A1PK401+561C" (road A-1, km 401.561, C = increasing km) with ETRS89
UTM 30N coordinates (the viewer's ``DB_DATA_CRS`` is EPSG:25830). That is the
live list and the one we read. Its JavaScript bundle also hard-codes a list in
WGS84 ("RADF004-401+561C", "A1- 401,5 - C"), one radar short of the API on
2026-10-01; we fall back on it when the API fails. The bundle's file name is
hashed and changes on each deploy, so we follow the viewer page to it. No speed
limit is published. The site answers only from a Spanish IP.

Most of these radars are DGT booths that the DGT file lists too (7 of 8 on
2026-10-01, 4 to 508 m apart, the km cut differently). Two zones for one radar
waste the phone's regions, so a radar on the same road within half a km of one
in the DGT file is left to the DGT source.

The Government of Navarra publishes no reuse licence for it, and the navarra.es
legal notice reserves the site's content. The feed reuses the data under Ley
37/2007 on the reuse of public-sector information (LICENSE-DATA.md).
"""

from __future__ import annotations

import json
import logging
import math
import re
import urllib.parse
import urllib.request

from .. import net
from ..geo import utm_to_wgs84
from ..model import Radar, SourceResult
from . import dgt
from .base import PUBLIC_SECTOR_REUSE, Context, Source

log = logging.getLogger(__name__)

VIEWER = "https://visorcontroltrafico.navarra.es/gn.visortrafico.web.internet/"
API = VIEWER + "api/openits/elements/radars"
LEGAL_URL = "https://www.navarra.es/es/aviso-legal"
ATTRIBUTION = "Gobierno de Navarra, Visor de Tráfico"
LICENCE = f"{PUBLIC_SECTOR_REUSE}. Its legal notice reserves the site's content: {LEGAL_URL}"
PROVINCE = "31"
UTM_ZONE = 30
# What the viewer sends (its config.json); the body is the viewer's own.
API_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "GNVisorTraficoInternet",
    "X-Api-Version": "1",
}
API_BODY = {"admin": False, "order": "-fecha", "filters": {}}
DIRECTIONS = {"C": "creciente", "D": "decreciente"}
# A radar on the same road this close (in km) to one in the DGT file is that radar.
SAME_RADAR_KM = 0.5

# "ALSASUA - A1PK401+561C": the part after "+" is the decimal part of the km,
# without trailing zeros ("N121APK25+9D" is km 25.9).
_API_NAME = re.compile(r"(?P<road>[A-Z]+\d+[A-Z]?)PK(?P<km>\d+)\+(?P<frac>\d+)(?P<dir>[CD])\s*$")
_ENTITY = re.compile(
    r'\{alias:"(?P<alias>RADF[^"]*)",nombre:"(?P<name>[^"]*)",'
    r"lat:(?P<lat>-?[\d.]+),lon:(?P<lon>-?[\d.]+)\}"
)
# "A1- 401,5 - C", "N121A - 25,9 - D"
_BUNDLE_NAME = re.compile(
    r"(?P<road>[A-Z]+\d+[A-Z]?)\s*-\s*(?P<km>\d+(?:,\d+)?)\s*-\s*(?P<dir>[CD])"
)
# "Radar fijo A-1 km 401.6 (sentido GUIPÚZCOA)": the road and km of a radar name.
_ROAD_KM = re.compile(r"^Radar (?:fijo|de tramo) (?P<road>\S+) km (?P<km>\d+(?:\.\d+)?)")
_SCRIPT = re.compile(r'<script[^>]+src="(?P<src>[^"]*assets/main-[^"]+\.js)"')


def road_name(code: str) -> str:
    """'A1' -> 'A-1', 'AP68' -> 'AP-68', 'N121A' -> 'N-121-A'."""
    m = re.fullmatch(r"([A-Z]+)(\d+)([A-Z]?)", code)
    if not m:
        return code
    return f"{m.group(1)}-{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")


def _radar(road: str, km: float, direction: str, lat: float, lon: float, url: str) -> Radar:
    # Both lists give the km to a different precision (the bundle cuts it to one
    # decimal), so name and id use one decimal, cut, and match in both.
    km1 = math.floor(km * 10 + 1e-6) / 10
    road = road_name(road)
    sense = DIRECTIONS[direction]
    return Radar(
        id=f"navarra-{road}-{km1:.1f}-{direction}",
        source="navarra",
        kind="fixed",
        name=f"Radar fijo {road} km {km1:.1f} (sentido {sense})",
        lat=lat,
        lon=lon,
        radius_m=500,
        url=url,
        attribution=ATTRIBUTION,
        direction=sense,
        province=PROVINCE,
    )


def parse_api(payload: bytes) -> list[Radar]:
    data = json.loads(payload)
    body = data.get("data") if isinstance(data, dict) else None
    if not isinstance(body, dict):
        raise ValueError("navarra: the API answer has no data object")
    radars = []
    for el in body.get("elemento") or []:
        m = _API_NAME.search(el.get("elemento", ""))
        point = el.get("punto") or {}
        if not m or "lon" not in point or "lat" not in point:
            log.warning("navarra: unreadable API element %r; skipped", el.get("elemento"))
            continue
        # The API names the UTM easting "lon" and the northing "lat".
        lat, lon = utm_to_wgs84(float(point["lon"]), float(point["lat"]), UTM_ZONE)
        km = float(f"{m.group('km')}.{m.group('frac')}")
        radars.append(_radar(m.group("road"), km, m.group("dir"), lat, lon, API))
    return radars


def bundle_url(viewer_html: str) -> str:
    m = _SCRIPT.search(viewer_html)
    if not m:
        raise ValueError("navarra: no main-*.js bundle on the viewer page")
    return urllib.parse.urljoin(VIEWER, m.group("src"))


def parse_bundle(js: str, url: str = VIEWER) -> list[Radar]:
    """The list hard-coded in the bundle; it is written twice, so each alias once."""
    radars: dict[str, Radar] = {}
    for m in _ENTITY.finditer(js):
        name = _BUNDLE_NAME.search(m.group("name"))
        if not name or m.group("alias") in radars:
            continue
        km = float(name.group("km").replace(",", "."))
        radars[m.group("alias")] = _radar(
            name.group("road"),
            km,
            name.group("dir"),
            float(m.group("lat")),
            float(m.group("lon")),
            url,
        )
    return list(radars.values())


def post_api(timeout: int = 60) -> bytes:
    """``net`` has no JSON POST, so the one call the viewer makes is done here."""
    request = urllib.request.Request(
        API, data=json.dumps(API_BODY).encode(), headers=API_HEADERS, method="POST"
    )
    with urllib.request.urlopen(request, timeout=net.timeout_s(timeout, API)) as response:
        return response.read()


def _road_km(name: str) -> tuple[str, float] | None:
    m = _ROAD_KM.match(name)
    return (m.group("road"), float(m.group("km"))) if m else None


def without_dgt(radars: list[Radar], dgt_radars: list[Radar]) -> list[Radar]:
    """The radars the DGT file does not list: same road, km within SAME_RADAR_KM."""
    known = [found for r in dgt_radars if (found := _road_km(r.name))]
    kept = []
    for radar in radars:
        here = _road_km(radar.name)
        if here and any(
            road == here[0] and abs(km - here[1]) <= SAME_RADAR_KM for road, km in known
        ):
            continue
        kept.append(radar)
    return kept


def _read(ctx: Context) -> list[Radar]:
    try:
        radars = parse_api(post_api())
        if not radars:
            raise ValueError("the API listed no radar")
        return radars
    except (OSError, ValueError) as exc:
        log.warning("navarra: radar API failed (%s); reading the viewer's bundle", exc)
    viewer = net.cached_get(VIEWER, max_age_s=ctx.max_age_s).decode("utf-8", "replace")
    url = bundle_url(viewer)
    js = net.cached_get(url, max_age_s=ctx.max_age_s).decode("utf-8", "replace")
    radars = parse_bundle(js, url)
    if not radars:
        raise ValueError(f"navarra: neither the API nor {url} lists a radar")
    return radars


def fetch(ctx: Context) -> SourceResult:
    radars = _read(ctx)
    try:
        # The same download as the DGT source's, from the shared cache.
        xml = net.cached_get(dgt.URL, max_age_s=dgt.SOURCE.max_age_s)
        dgt_radars = dgt.parse(xml, {PROVINCE})
    except Exception as exc:  # a radar listed twice beats a radar missing
        log.warning("navarra: DGT file unreadable (%s); keeping every radar, some twice", exc)
        return SourceResult(radars=radars)
    kept = without_dgt(radars, dgt_radars)
    log.info("navarra: %d radars, %d of them in the DGT file", len(radars), len(radars) - len(kept))
    return SourceResult(radars=kept)


SOURCE = Source(
    key="navarra",
    cameras=True,
    fetch=fetch,
    attribution=ATTRIBUTION,
    licence=LICENCE,
    spanish_ip_hosts=frozenset({"navarra.es"}),
    max_age_s=86_400,
    provinces=frozenset({PROVINCE}),
)
