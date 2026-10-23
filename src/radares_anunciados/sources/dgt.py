"""DGT fixed radars and average-speed sections from the DGT NAP (DATEX II).

Dataset: https://nap.dgt.es/dataset/radares-fijos-dgt, license CC BY 4.0,
attribution "Dirección General de Tráfico". State roads only: the Basque
Country and Catalonia run their own.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from .. import net
from ..model import SPAIN, Radar, SourceResult, Stretch, dated
from .base import Context, Source

# The same file on two hosts, read in this order. infocar answered HTTP 502 from
# 2026-10-21 while NAP served the same 2,051,128 bytes. NAP sends no
# Last-Modified, so the file's own <publicationTime> dates it (``published``).
URLS = (
    "https://nap.dgt.es/datex2/dgt/PredefinedLocationsPublication/radares/content.xml",
    "https://infocar.dgt.es/datex2/dgt/PredefinedLocationsPublication/radares/content.xml",
)
URL = URLS[0]  # the `url` of every record: the host read first
ATTRIBUTION = "Dirección General de Tráfico (CC BY 4.0)"

_D = "{http://datex2.eu/schema/1_0/1_0}"
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
# "positive": the radar watches traffic going up the km markers; "negative": down.
KM_DIRECTIONS = frozenset({"positive", "negative", "both"})
# <publicationTime> sits in the file's first lines; read with a regex on the head
# so dating a 2 MB file costs no second parse.
_PUBLICATION_TIME = re.compile(rb"<(?:\w+:)?publicationTime>\s*([^<\s]+)\s*<")

log = logging.getLogger(__name__)


def _text(el: ET.Element, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


def _coords(point: ET.Element) -> tuple[float, float]:
    return (
        float(_text(point, f"{_D}pointCoordinates/{_D}latitude")),
        float(_text(point, f"{_D}pointCoordinates/{_D}longitude")),
    )


def _km(ref: ET.Element | None) -> float | None:
    dist = _text(ref, f"{_D}referencePointDistance") if ref is not None else None
    return round(float(dist) / 1000, 3) if dist else None


def _label(ref: ET.Element | None) -> str:
    """'A-7 km 580.3 (sentido ALMERIA)' from a DATEX referencePoint."""
    if ref is None:
        return ""
    road = _text(ref, f"{_D}roadNumber") or "?"
    label = road
    dist = _text(ref, f"{_D}referencePointDistance")
    if dist:
        label += f" km {float(dist) / 1000:.1f}"
    direction = _text(ref, f".//{_D}directionNamed")
    if direction:
        label += f" (sentido {direction})"
    return label


def parse(
    xml: bytes, provinces: set[str] | frozenset[str] | None, radius_m: int = 500
) -> list[Radar]:
    """Radars in the given INE province codes (e.g. {"30"} for Murcia; None: all)."""
    return parse_all(xml, provinces, radius_m).radars


def parse_all(
    xml: bytes, provinces: set[str] | frozenset[str] | None, radius_m: int = 500
) -> SourceResult:
    """Radars, and each average-speed section as a stretch, in ``provinces``."""
    root = ET.fromstring(xml)
    radars: list[Radar] = []
    stretches: list[Stretch] = []
    for loc_set in root.iter(f"{_D}predefinedLocationSet"):
        for loc in loc_set.findall(f"{_D}predefinedLocation"):
            # The file writes Alicante as "3"; an INE code is "03".
            province = _text(loc, f".//{_D}provinceINEIdentifier")
            province = province.zfill(2) if province else None
            if provinces is not None and province not in provinces:
                continue
            inner = loc.find(f"{_D}predefinedLocation")
            if inner is None:
                continue
            source_id = loc.get("id", "").removeprefix("GUID_")
            kind = inner.get(_XSI_TYPE, "").split(":")[-1]
            direction = _text(inner, f".//{_D}directionNamed")
            if kind == "Point":
                point = inner.find(f"{_D}tpegpointLocation/{_D}point")
                lat, lon = _coords(point)
                ref = inner.find(f"{_D}referencePoint")
                label = _label(ref)
                relative = _text(ref, f".//{_D}directionRelative") if ref is not None else None
                radars.append(
                    Radar(
                        id=f"dgt-{source_id}",
                        source="dgt",
                        kind="fixed",
                        name=f"Radar fijo {label}",
                        lat=lat,
                        lon=lon,
                        radius_m=radius_m,
                        url=URL,
                        attribution=ATTRIBUTION,
                        direction=direction,
                        province=province,
                        road_ref=_text(ref, f"{_D}roadNumber") if ref is not None else None,
                        km=_km(ref),
                        km_direction=relative if relative in KM_DIRECTIONS else None,
                    )
                )
            elif kind == "Linear":
                # An average-speed section has a camera at each end; direction
                # is "unknown" in the data, so both ends get a circle.
                linear = inner.find(f"{_D}tpeglinearLocation")
                primary = inner.find(f".//{_D}referencePointPrimaryLocation/{_D}referencePoint")
                second = inner.find(f".//{_D}referencePointSecondaryLocation/{_D}referencePoint")
                start = _label(primary)
                ends = {}
                for end in ("from", "to"):
                    point = linear.find(f"{_D}{end}")
                    if point is None:
                        continue
                    ends[end] = lat, lon = _coords(point)
                    radars.append(
                        Radar(
                            id=f"dgt-{source_id}-{end}",
                            source="dgt",
                            kind="section",
                            name=f"Radar de tramo {start}",
                            lat=lat,
                            lon=lon,
                            radius_m=radius_m,
                            url=URL,
                            attribution=ATTRIBUTION,
                            direction=direction,
                            province=province,
                        )
                    )
                if len(ends) == 2:
                    stretches.append(
                        Stretch(
                            id=f"dgt-{source_id}",
                            source="dgt",
                            name=f"Tramo {start}",
                            road=_text(primary, f"{_D}roadNumber") if primary is not None else None,
                            start=ends["from"],
                            end=ends["to"],
                            km_from=_km(primary),
                            km_to=_km(second),
                            direction=direction,
                            province=province,
                            url=URL,
                            attribution=ATTRIBUTION,
                        )
                    )
    return SourceResult(radars=radars, stretches=stretches)


def publication_time(xml: bytes) -> datetime | None:
    """The file's <publicationTime> ("2025-12-18T09:56:52.406+01:00"), or None.
    A time written without a UTC offset is read in Spain's zone, the publisher's,
    so the day and the age always come from the same instant."""
    m = _PUBLICATION_TIME.search(xml[:4096])
    if not m:
        return None
    try:
        when = datetime.fromisoformat(m.group(1).decode("ascii"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=SPAIN)


def get(max_age_s: int = 86_400) -> tuple[bytes, str | None]:
    """The file and the Last-Modified it came with, from the first host in ``URLS``
    that answers (through the shared cache). Raises only when every host fails."""
    errors = []
    for url in URLS:
        try:
            return net.cached_get_dated(url, max_age_s=max_age_s)
        except Exception as exc:
            log.warning("dgt: %s failed (%s); trying the next host", url, exc)
            errors.append(f"{url}: {exc}")
    raise OSError("dgt: no host answered: " + "; ".join(errors))


def published_day(xml: bytes, modified: str | None) -> str | None:
    """The day the file says it was published, else the day of ``modified`` (the
    Last-Modified it came with), else None."""
    when = publication_time(xml)
    return when.date().isoformat() if when else net.last_modified_day(modified)


def download(max_age_s: int = 86_400) -> tuple[bytes, str | None]:
    """(the file, the day it was published). The day comes from the bytes in use,
    so a cached copy never takes the date of a newer file."""
    xml, modified = get(max_age_s)
    return xml, published_day(xml, modified)


def fetch(ctx: Context) -> SourceResult:
    xml, day = download(ctx.max_age_s)
    return dated(parse_all(xml, ctx.provinces), day)


SOURCE = Source(
    key="dgt",
    cameras=True,
    fetch=fetch,
    attribution=ATTRIBUTION,
    licence="CC BY 4.0",
    max_age_s=86_400,
)
