"""Speed cameras mapped in OpenStreetMap: ``highway=speed_camera`` nodes and the
``type=enforcement`` relations around them.

Two Overpass queries: the cameras, then the relations. The relations query is
the heavier one. When it fails the last relations answer is used, and with none
the cameras come alone, so a new install is never left without OSM cameras.

An enforcement relation (https://wiki.openstreetmap.org/wiki/Relation:enforcement)
ties a ``device`` (the camera) to a ``from`` node on the road where the control
starts and, optionally, a ``to`` node where it ends; without a ``to``, the
device is the end. Two kinds are read, ``enforcement=maxspeed`` and
``average_speed``:

- ``maxspeed``: the camera gets the relation's limit and the bearing from
  ``from`` to the end as its direction, unless the node has its own tags.
  Bearings within ``BEARING_TOLERANCE`` of each other agree and give their
  mean; a camera whose relations point different ways, or give different
  limits, gets none: a wrong one is worse than none.
- ``average_speed``: a section, drawn as its two ends (kind ``section``, both
  named ``Radar de tramo …``) and a line between them. A camera of the
  relation within ``END_M`` of its ``from`` or ``to`` node is that end: the end
  goes where the camera is, which is what an authority publishes, and the
  camera is not added again. An end with no camera goes on the node. A section
  with more than one ``from`` or ``to`` says no single start and end; its
  cameras stay plain ones.
- A ``device`` node tagged as no ``highway`` at all (a bare node, a
  ``man_made=surveillance``) is a camera too; one tagged as something else
  (``highway=speed_display``) is not.

Data (c) OpenStreetMap contributors, ODbL 1.0. A feed that includes these
points is a derived database and must stay under ODbL.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter

from .. import net
from ..geo import bearing_deg, distance_m
from ..model import Radar, SourceResult, Stretch
from ..provinces import Box
from ..streets import maxspeed_kmh
from .base import Context, Source

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
ENFORCEMENT = ("maxspeed", "average_speed")
END_M = 150  # a section's camera this close to one of its ends is that end
MIN_SPAN_M = 5  # closer than this, two nodes give no direction
BEARING_TOLERANCE = 20  # degrees: two relations this close point the same way

_ROAD_KM = re.compile(r"\b([A-Z]{1,3}-\d{1,4})\s+Km\.?\s*([\d.,]+)", re.IGNORECASE)


# Región de Murcia plus a margin, (south, west, north, east). A bounding box
# is far cheaper for Overpass than an area lookup, which 504s under load.
MURCIA_REGION = (37.37, -2.35, 38.76, -0.64)


def query(bbox: Box) -> str:
    """Overpass QL for every speed camera node in a bounding box."""
    south, west, north, east = bbox
    return (
        f"[out:json][timeout:60][bbox:{south},{west},{north},{east}];"
        'node["highway"="speed_camera"];'
        "out body;"
    )


def query_boxes(boxes: list[Box] | tuple[Box, ...]) -> str:
    """One query over several boxes (all of Spain is 52 province boxes). One box
    gives exactly ``query``, so its cached answer stays valid."""
    if len(boxes) == 1:
        return query(boxes[0])
    parts = "".join(f'node["highway"="speed_camera"]({s},{w},{n},{e});' for s, w, n, e in boxes)
    return f"[out:json][timeout:180];({parts});out body;"


def relations_query(boxes: list[Box] | tuple[Box, ...]) -> str:
    """Every enforcement relation with a member in a box, the member nodes, which
    carry the positions, and the tags of its ``section`` ways, which name the road."""
    kinds = "|".join(ENFORCEMENT)
    relations = "".join(
        f'relation["type"="enforcement"]["enforcement"~"^({kinds})$"]({s},{w},{n},{e});'
        for s, w, n, e in boxes
    )
    return (
        f"[out:json][timeout:180];({relations})->.r;node(r.r)->.m;"
        'way(r.r:"section")->.w;.m out body;.w out tags;.r out body;'
    )


def _road_km(tags: dict[str, str]) -> str | None:
    for key in ("note", "description", "name", "ref"):
        match = _ROAD_KM.search(tags.get(key, ""))
        if match:
            return f"{match.group(1).upper()} km {match.group(2).replace(',', '.')}"
    return None


def _name(tags: dict[str, str]) -> str:
    road_km = _road_km(tags)
    # the zone name adds the limit when the camera has one
    return f"Radar {road_km}" if road_km else "Radar"


def _point(node: dict) -> tuple[float, float]:
    return node["lat"], node["lon"]


def _roles(relation: dict, nodes: dict[int, dict]) -> dict[str, list[dict]]:
    """The relation's member nodes by role, those in the answer only."""
    out: dict[str, list[dict]] = {"device": [], "from": [], "to": []}
    for m in relation.get("members", []):
        if m.get("type") == "node" and m.get("role") in out and m.get("ref") in nodes:
            out[m["role"]].append(nodes[m["ref"]])
    return out


def _road(relation: dict, ways: dict[int, dict[str, str]]) -> str | None:
    """The road of a section: the ``ref`` most of its ``section`` ways carry, or
    their ``name``; ties go to the first in order, so the answer is stable."""
    tags = [
        ways[m["ref"]]
        for m in relation.get("members", [])
        if m.get("type") == "way" and m.get("role") == "section" and m.get("ref") in ways
    ]
    for key in ("ref", "name"):
        values = [t[key].split(";")[0].strip() for t in tags if t.get(key)]
        if values:
            return sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return None


def _end(node: dict, devices: list[dict]) -> tuple[float, float]:
    """Where a section end goes: the relation's camera at that end, the position an
    authority publishes too, or without one the node on the road."""
    camera = min(devices, key=lambda d: distance_m(_point(d), _point(node)), default=None)
    if camera is not None and distance_m(_point(camera), _point(node)) <= END_M:
        return _point(camera)
    return _point(node)


def _bearing(start: dict, end: dict) -> int | None:
    if distance_m(_point(start), _point(end)) < MIN_SPAN_M:
        return None
    return bearing_deg(_point(start), _point(end))


def _agreed(values: list) -> object | None:
    """The one value every relation gives, or None when they differ or give none."""
    found = {v for v in values if v is not None}
    return found.pop() if len(found) == 1 else None


def _agreed_bearing(values: list[int | None]) -> str | None:
    """The mean of bearings that agree within ``BEARING_TOLERANCE``, as the
    ``direction`` tag writes it ("75"); None when any two point different ways."""
    found = [v for v in values if v is not None]
    if not found:
        return None
    if any(abs((a - b + 180) % 360 - 180) > BEARING_TOLERANCE for a in found for b in found):
        return None
    x = sum(math.cos(math.radians(v)) for v in found)
    y = sum(math.sin(math.radians(v)) for v in found)
    return str(round(math.degrees(math.atan2(y, x))) % 360)


def parse_all(
    cameras_payload: bytes, relations_payload: bytes | None = None, radius_m: int = 500
) -> SourceResult:
    """The cameras of the cameras query, with what the relations add when there
    is a relations answer."""
    camera_nodes = [
        el for el in json.loads(cameras_payload).get("elements", []) if el.get("type") == "node"
    ]
    elements = json.loads(relations_payload).get("elements", []) if relations_payload else []
    nodes = {el["id"]: el for el in elements if el.get("type") == "node"}
    nodes.update({el["id"]: el for el in camera_nodes})
    ways = {el["id"]: el.get("tags", {}) for el in elements if el.get("type") == "way"}
    relations = sorted(
        (
            el
            for el in elements
            if el.get("type") == "relation" and el.get("tags", {}).get("enforcement") in ENFORCEMENT
        ),
        key=lambda el: el["id"],
    )
    cameras = {i: n for i, n in nodes.items() if n.get("tags", {}).get("highway") == "speed_camera"}
    bearings: dict[int, list[int | None]] = {}
    limits: dict[int, list[int | None]] = {}
    radars: list[Radar] = []
    stretches: list[Stretch] = []
    ends_of: set[int] = set()  # cameras that are the end of a section
    for rel in relations:
        tags = rel.get("tags", {})
        roles = _roles(rel, nodes)
        for device in roles["device"]:
            if "highway" not in device.get("tags", {}):
                cameras.setdefault(device["id"], device)
        limit = maxspeed_kmh(tags.get("maxspeed"))
        starts, ends = roles["from"], roles["to"]
        if tags["enforcement"] == "maxspeed":
            for device in roles["device"]:
                bearing = None
                if len(starts) == 1 and len(ends) <= 1:
                    bearing = _bearing(starts[0], ends[0] if ends else device)
                bearings.setdefault(device["id"], []).append(bearing)
                limits.setdefault(device["id"], []).append(limit)
            continue
        ends = ends or roles["device"]  # without a "to", the camera is the end
        if len(starts) != 1 or len(ends) != 1:
            continue  # no single start and end
        start, end = _end(starts[0], roles["device"]), _end(ends[0], roles["device"])
        if start == end:  # one camera near both ends of a short section: the road nodes
            start, end = _point(starts[0]), _point(ends[0])
        # The road and km when a tag gives them. Else the road of the section's
        # ways and the relation id: the blueprint alerts once per name, and the
        # A-7 alone has 8 mapped sections, so "Radar de tramo A-7" would stay
        # silent at the next one. With no road, the id alone.
        found = [_road_km(n.get("tags", {})) for n in [rel, *roles["device"]]]
        road = _road(rel, ways)
        osm_id = f"(OSM {rel['id']})"
        label = next((x for x in found if x), f"{road} {osm_id}" if road else osm_id)
        url = f"https://www.openstreetmap.org/relation/{rel['id']}"
        bearing = _bearing(starts[0], ends[0])
        direction = None if bearing is None else str(bearing)
        for which, (lat, lon) in (("from", start), ("to", end)):
            radars.append(
                Radar(
                    id=f"osm-relation-{rel['id']}-{which}",
                    source="osm",
                    kind="section",
                    name=f"Radar de tramo {label}",
                    lat=lat,
                    lon=lon,
                    radius_m=radius_m,
                    url=url,
                    attribution=ATTRIBUTION,
                    maxspeed=limit,
                    direction=direction,
                )
            )
        stretches.append(
            Stretch(
                id=f"osm-relation-{rel['id']}",
                source="osm",
                name=f"Tramo {label}",
                road=road,
                start=start,
                end=end,
                maxspeed=limit,
                direction=direction,
                url=url,
                attribution=ATTRIBUTION,
            )
        )
        for device in roles["device"]:
            if min(distance_m(_point(device), _point(n)) for n in (starts[0], ends[0])) <= END_M:
                ends_of.add(device["id"])
    for i, node in sorted(cameras.items()):
        if i in ends_of:
            continue
        tags = node.get("tags", {})
        radars.append(
            Radar(
                id=f"osm-{i}",
                source="osm",
                kind="fixed",
                name=_name(tags),
                lat=node["lat"],
                lon=node["lon"],
                radius_m=radius_m,
                url=f"https://www.openstreetmap.org/node/{i}",
                attribution=ATTRIBUTION,
                maxspeed=maxspeed_kmh(tags.get("maxspeed")) or _agreed(limits.get(i, [])),
                direction=tags.get("direction") or _agreed_bearing(bearings.get(i, [])),
            )
        )
    return SourceResult(radars=radars, stretches=stretches)


def parse(payload: bytes, radius_m: int = 500) -> list[Radar]:
    return parse_all(payload, radius_m=radius_m).radars


def fetch(ctx: Context) -> SourceResult:
    cameras = net.cached_get(
        OVERPASS_URL,
        {"data": query_boxes(ctx.boxes)},
        max_age_s=ctx.max_age_s,
        validate=net.overpass_answer,
    )
    asked = {"data": relations_query(ctx.boxes)}
    try:
        relations = net.cached_get(
            OVERPASS_URL, asked, max_age_s=ctx.max_age_s, validate=net.overpass_answer
        )
    except (OSError, ValueError) as exc:  # the cameras matter more than what relations add
        # The last relations answer keeps the sections and their zones; with none
        # (a new install, the published feed's empty cache) the cameras come alone.
        relations = net.cached_copy(OVERPASS_URL, asked, validate=net.overpass_answer)
        log.warning(
            "OSM enforcement relations failed (%s); %s",
            exc,
            "using the last answer" if relations else "cameras only this run",
        )
    return parse_all(cameras, relations)


SOURCE = Source(
    key="osm",
    cameras=True,
    fetch=fetch,
    attribution=ATTRIBUTION,
    licence="ODbL 1.0",
    max_age_s=86_400,
    official=False,
)
