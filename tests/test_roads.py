"""roads.json: polylines, the upstream walk, snapping, stretch lines, gates and the
gates that refuse to publish. Synthetic road networks, no OSM file, no network."""

import json
import math

import pytest

from radares_anunciados.geo import bearing_deg, distance_m
from radares_anunciados.roads import build, pbf
from radares_anunciados.roads.graph import Directed, angle_diff, length_m
from radares_anunciados.roads.pbf import Network
from radares_anunciados.roads.polyline import decode, encode, simplify
from radares_anunciados.roads.snap import snap

ORIGIN = (37.0, -1.0)
PRIMARY, RESIDENTIAL = pbf._CODE["primary"], pbf._CODE["residential"]


def xy(x_m: float, y_m: float) -> tuple[float, float]:
    """(lat, lon) ``x_m`` east and ``y_m`` north of ``ORIGIN``."""
    lat = ORIGIN[0] + y_m / 111_195
    lon = ORIGIN[1] + x_m / (111_195 * math.cos(math.radians(ORIGIN[0])))
    return (lat, lon)


class Builder:
    def __init__(self, bbox=None):
        self.net = Network(bbox)
        self.next_id = 1
        self.at: dict[tuple[float, float], int] = {}

    def node(self, x, y):
        if (x, y) not in self.at:
            self.at[(x, y)] = self.next_id
            self.next_id += 1
        return self.at[(x, y)]

    def way(self, pts, code=PRIMARY, ref="", oneway=0, step=None):
        if step:  # vertices every ``step`` metres along each straight piece
            dense = [pts[0]]
            for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
                n = max(1, round(math.hypot(bx - ax, by - ay) / step))
                dense += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(1, n + 1)]
            pts = dense
        refs = frozenset(r for r in ref.upper().split(";") if r)
        return self.net.add_way(
            [self.node(x, y) for x, y in pts], [xy(x, y) for x, y in pts], code, refs, oneway
        )


# --- polylines ---------------------------------------------------------------------


def test_the_polyline_matches_googles_reference_vector():
    points = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert encode(points) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == points


def test_a_polyline_round_trips_to_five_decimals():
    points = [(37.123456, -1.987654), (37.0, -1.0), (36.99999, -0.99999), (-10.5, 170.25)]
    back = decode(encode(points))
    for p, q in zip(points, back, strict=True):
        assert abs(p[0] - q[0]) <= 0.5e-5 and abs(p[1] - q[1]) <= 0.5e-5
    with pytest.raises(ValueError):
        decode(encode(points)[:-1] + "_")  # a chunk that never ends


def test_douglas_peucker_keeps_the_ends_and_drops_a_straight_midpoint():
    straight = [xy(0, 0), xy(0, 500), xy(0, 1000)]
    assert simplify(straight, 5) == [straight[0], straight[-1]]
    bent = [xy(0, 0), xy(20, 500), xy(0, 1000)]
    assert simplify(bent, 5) == bent  # 20 m off the chord: kept
    assert simplify(bent, 25) == [bent[0], bent[-1]]


# --- the upstream walk ---------------------------------------------------------------


def crossroads(turn_way=None):
    """A radar road heading north from junction J at (0, 0), ref N-1. Into J: N-1 from
    the south, C-1 from the west (a 90 degree turn), a residential lane from the east."""
    b = Builder()
    radar_road = b.way([(0, 0), (0, 600)], ref="N-1", step=100)
    b.way([(0, -3000), (0, 0)], ref="N-1", step=250)
    b.way([(-3000, 0), (0, 0)], ref="C-1", step=250)
    b.way([(3000, 0), (0, 0)], code=RESIDENTIAL, step=250)
    if turn_way:
        b.way(turn_way, step=100)
    return b, radar_road


def walk_from(b, way, point_xy, travel=0.0, metres=1500):
    net = b.net
    at = snap(net, xy(*point_xy))
    assert at is not None and at.way == way
    node = net.way_nodes(way)[at.seg]
    return Directed(net, range(len(net))).upstream(at.point, node, way, travel, metres)


def test_the_upstream_walk_keeps_to_the_same_road_number():
    b, radar_road = crossroads()
    out = walk_from(b, radar_road, (0, 300))
    assert out[0] == pytest.approx(xy(0, 300), abs=1e-7)
    assert abs(length_m(out) - 1500) < 1  # stops at 1,500 m of road
    end = out[-1]
    assert distance_m(end, xy(0, -1200)) < 2  # straight on along N-1, not C-1 or the lane


def test_the_upstream_walk_takes_the_same_road_over_a_straighter_one():
    b = Builder()
    radar_road = b.way([(0, 0), (0, 600)], ref="N-1", step=100)
    b.way([(0, -3000), (0, 0)], step=250)  # straight on, no ref
    b.way([(-2600, -1500), (0, 0)], ref="N-1", step=250)  # the road itself, turning 60 degrees
    out = walk_from(b, radar_road, (0, 300))
    assert out[-1][1] < xy(-500, 0)[1]  # went west with N-1


def test_the_upstream_walk_refuses_a_turn_over_100_degrees():
    b = Builder()
    radar_road = b.way([(0, 0), (0, 600)], ref="N-1", step=100)
    # the only road into J arrives heading 120 degrees from north: a 120 degree turn
    b.way([(-866, 500), (0, 0)], ref="N-1", step=100)
    out = walk_from(b, radar_road, (0, 300))
    assert distance_m(out[-1], xy(0, 0)) < 1  # stopped at the junction
    assert abs(length_m(out) - 300) < 1
    # positive control: arriving at 80 degrees is taken
    b2 = Builder()
    radar_road = b2.way([(0, 0), (0, 600)], ref="N-1", step=100)
    b2.way([(-985, -174), (0, 0)], ref="N-1", step=100)
    assert length_m(walk_from(b2, radar_road, (0, 300))) > 1000


def test_the_upstream_walk_respects_one_way_roads():
    b = Builder()
    radar_road = b.way([(0, 0), (0, 600)], step=100)
    b.way([(0, 0), (0, -2000)], oneway=1, step=250)  # leaves J southwards only
    out = walk_from(b, radar_road, (0, 300))
    assert distance_m(out[-1], xy(0, 0)) < 1


# --- snapping ---------------------------------------------------------------------------


def test_snap_prefers_the_named_road_within_40_m_and_nothing_beyond():
    b = Builder()
    near = b.way([(-500, 5), (500, 5)])
    named = b.way([(-500, 30), (500, 30)], ref="A-7")
    far = Builder()
    far.way([(-500, 50), (500, 50)], ref="A-7")
    p = xy(0, 0)
    assert snap(b.net, p).way == near
    assert snap(b.net, p, frozenset({"A-7"})).way == named
    assert snap(far.net, p, frozenset({"A-7"})) is None
    alone = Builder()
    w = alone.way([(-500, 5), (500, 5)])
    alone.way([(-500, 50), (500, 50)], ref="A-7")  # the road named, but 50 m off
    assert snap(alone.net, p, frozenset({"A-7"})).way == w


# --- stretches ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "km, accepted", [(1.0, True), (1.031, True), (1.25, False), (0.833, False)]
)
def test_the_sidecar_band_accepts_097_and_refuses_080_and_120(km, accepted):
    b = Builder()
    b.way([(0, -500), (0, 1500)], ref="N-1", step=100)
    line = build._route(b.net, xy(0, 0), xy(0, 1000), frozenset({"N-1"}), km)
    assert (line is not None) == accepted
    if accepted:
        assert line[0] == xy(0, 0) and line[-1] == xy(0, 1000)


def test_a_stretch_without_a_road_number_must_stay_on_its_class():
    b = Builder()
    b.way([(0, -200), (0, 400)], step=100)  # primary
    b.way([(0, 400), (0, 1200)], code=RESIDENTIAL, step=100)
    assert build._route(b.net, xy(0, 0), xy(0, 1000), frozenset(), None) is None
    ok = Builder()
    ok.way([(0, -200), (0, 1200)], step=100)
    assert build._route(ok.net, xy(0, 0), xy(0, 1000), frozenset(), None) is not None
    # and no longer than 1.6 x the chord + 500 m: a 3 km detour for a 1 km chord is refused
    detour = Builder()
    detour.way([(0, 0), (1500, 0), (1500, 1000), (0, 1000)], step=100)
    assert build._route(detour.net, xy(0, 0), xy(0, 1000), frozenset(), None) is None


def test_the_line_never_starts_by_driving_backwards():
    """route() joins a published end to the nearest node; on a road with nodes 400 m
    apart that node can lie behind the end."""
    line = [xy(0, 0), xy(0, -150), xy(0, 400), xy(0, 800), xy(0, 1150), xy(0, 1000)]
    assert build.trim_ends(line) == [xy(0, 0), xy(0, 400), xy(0, 800), xy(0, 1000)]


def feature(fid, geometry, **props):
    return {"type": "Feature", "id": fid, "geometry": geometry, "properties": props}


def point(fid, x, y, kind="fixed", **props):
    lat, lon = xy(x, y)
    return feature(fid, {"type": "Point", "coordinates": [lon, lat]}, kind=kind, **props)


def stretch(fid, a, b, **props):
    (la, oa), (lb, ob) = xy(*a), xy(*b)
    geometry = {"type": "LineString", "coordinates": [[oa, la], [ob, lb]]}
    return feature(fid, geometry, kind="stretch", **props)


def two_way_network():
    b = Builder()
    b.way([(0, -3000), (0, 6000)], ref="N-1", step=200)
    return b


def compute(features, net):
    return build.compute({"type": "FeatureCollection", "features": features}, net, "2026-10-20", 0)


def test_a_stretch_gets_its_road_and_gates_that_drive_into_it():
    b = two_way_network()
    result = compute([stretch("s", (0, 0), (0, 3000), road="N-1", km_from=10.0, km_to=13.0)], b.net)
    entry = json.loads(result.roads["text"])["features"]["s"]
    line = decode(entry["line"])
    assert line[0] == pytest.approx(xy(0, 0), abs=1e-5)
    assert line[-1] == pytest.approx(xy(0, 3000), abs=1e-5)
    (start,), (end,) = entry["gates"]["start"], entry["gates"]["end"]
    start_pts, end_pts = decode(start["polyline"]), decode(end["polyline"])
    # ends at the stretch's end, driving into it: north at the start, south at the end
    assert distance_m(start_pts[-1], xy(0, 0)) < 2 and distance_m(end_pts[-1], xy(0, 3000)) < 2
    assert angle_diff(bearing_deg(start_pts[0], start_pts[-1]), 0) < 5 and start["bearing"] == 0
    assert angle_diff(bearing_deg(end_pts[0], end_pts[-1]), 180) < 5 and end["bearing"] == 180
    assert abs(length_m(start_pts) - 1500) < 3
    assert result.block["stretches_followed"] == result.block["stretches_with_gates"] == 1


def test_a_point_gets_one_approach_per_direction_on_a_two_way_road():
    b = two_way_network()
    result = compute([point("p", 0, 2000, source="osm")], b.net)
    entry = json.loads(result.roads["text"])["features"]["p"]
    assert sorted(a["bearing"] for a in entry["approaches"]) == [0, 180]
    for a in entry["approaches"]:
        pts = decode(a["polyline"])
        assert distance_m(pts[-1], xy(0, 2000)) < 2  # ends at the radar
        assert abs(length_m(pts) - 1500) < 3
    assert "p" not in result.features  # no signal: no bearing


def test_a_bearing_gives_one_approach_and_the_feed_properties():
    b = two_way_network()
    result = compute([point("p", 0, 2000, source="osm", direction="175")], b.net)
    (only,) = json.loads(result.roads["text"])["features"]["p"]["approaches"]
    assert only["bearing"] == 180
    assert result.features["p"] == {
        "bearing": 180,
        "direction_semantics": "travel",
        "direction_confidence": "low",
        "bearing_source": "osm_tag",
    }


def test_both_directions_publish_no_bearing_and_two_approaches():
    b = two_way_network()
    result = compute([point("p", 0, 2000, source="dgt", direction="both")], b.net)
    assert len(json.loads(result.roads["text"])["features"]["p"]["approaches"]) == 2
    assert result.features == {}


def test_a_section_point_on_a_stretch_end_gets_no_entry():
    b = two_way_network()
    s = stretch("s", (0, 0), (0, 3000), road="N-1")
    lon, lat = s["geometry"]["coordinates"][0]
    folded = feature("s-from", {"type": "Point", "coordinates": [lon, lat]}, kind="section")
    loose = point("loose", 0, 4500, kind="section")
    found = json.loads(compute([s, folded, loose], b.net).roads["text"])["features"]
    assert "s-from" not in found and "loose" in found


def test_kinds_without_a_place_on_the_road_get_nothing():
    b = two_way_network()
    feats = [point("m", 0, 2000, kind="mobile_announced"), point("r", 0, 2000, kind="reported")]
    assert json.loads(compute(feats, b.net).roads["text"])["features"] == {}


def test_roads_json_is_the_same_byte_for_byte_on_every_run():
    b = two_way_network()
    feats = [
        stretch("s", (0, 0), (0, 3000), road="N-1", km_from=10.0, km_to=13.0),
        point("p", 0, 4500, source="osm"),
        point("q", 0, 5000, source="osm", direction="0"),
    ]
    first = compute(feats, b.net).roads["text"]
    assert compute(list(reversed(feats)), b.net).roads["text"] == first
    doc = json.loads(first)
    assert list(doc) == sorted(doc) and doc["version"] == 1 and doc["precision"] == 5
    assert doc["attribution"] == "© OpenStreetMap contributors (ODbL 1.0)"
    assert list(doc["features"]) == sorted(doc["features"])


# --- the gates that stop a bad publish -----------------------------------------------


def test_the_size_gate_fails_an_oversized_file(monkeypatch):
    b = two_way_network()
    feats = [point("p", 0, 2000, source="osm")]
    compute(feats, b.net)  # passes as is
    monkeypatch.setattr(build, "MAX_BYTES", 100)
    with pytest.raises(build.RoadsError, match="over 100"):
        compute(feats, b.net)
    monkeypatch.setattr(build, "MAX_BYTES", 2_000_000)
    monkeypatch.setattr(build, "MAX_GZIP_BYTES", 50)
    with pytest.raises(build.RoadsError, match="gzipped"):
        compute(feats, b.net)


def test_the_coverage_gates_fail_when_too_little_is_followed():
    b = two_way_network()
    on_road = [point(f"p{i}", 0, 200 * i, source="osm") for i in range(3)]
    off_road = [point(f"x{i}", 900, 200 * i, source="osm") for i in range(3)]
    compute(on_road + off_road[:1], b.net)  # 3 of 4: passes
    with pytest.raises(build.RoadsError, match="3 of 6 radars"):
        compute(on_road + off_road, b.net)
    lost = [stretch(f"s{i}", (900, 0), (900, 2000), road="N-9") for i in range(3)]
    with pytest.raises(build.RoadsError, match="0 of 3 stretches"):
        compute(lost, b.net)


def test_coverage_counts_only_what_lies_inside_the_extract():
    """A clip of one province must not fail because the rest of Spain has no roads in it."""
    box = (xy(-1000, -4000)[1], xy(-1000, -4000)[0], xy(1000, 7000)[1], xy(1000, 7000)[0])
    b = Builder(bbox=box)
    b.way([(0, -3000), (0, 6000)], ref="N-1", step=200)
    inside = [point(f"in{i}", 0, 500 * i, source="osm") for i in range(10)]
    far = [point(f"far{i}", 50_000, 1000 * i, source="osm") for i in range(100)]
    result = compute(inside + far, b.net)
    assert result.block["points_in_bbox"] == 10
    assert result.block["points_with_approaches"] == 10
    assert result.block["bbox"] == list(box)
    # positive control: with no box, the same feed fails the gate
    b.net.bbox = None
    with pytest.raises(build.RoadsError, match="10 of 110"):
        compute(inside + far, b.net)


def test_enrich_adds_properties_at_the_end_and_credits_osm_once():
    feed = {
        "type": "FeatureCollection",
        "features": [
            point("a", 0, 0, source="dgt", attribution="DGT"),
            point("b", 0, 0, source="osm", attribution="© OpenStreetMap contributors (ODbL 1.0)"),
            point("c", 0, 0, source="dgt", attribution="DGT"),
        ],
    }
    before = json.loads(json.dumps(feed))
    out = build.enrich(feed, {"a": {"bearing": 12}}, {"a", "b"})
    a, b2, c = out["features"]
    assert list(a["properties"])[-1] == "bearing"
    assert a["properties"]["attribution"] == "DGT; road: © OpenStreetMap contributors (ODbL 1.0)"
    assert b2["properties"] == before["features"][1]["properties"]
    assert c == before["features"][2]
    assert [f["geometry"] for f in out["features"]] == [f["geometry"] for f in before["features"]]
