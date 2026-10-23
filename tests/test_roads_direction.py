"""The direction signals (official km, carriageway, OSM tag) and the decision."""

import math

import pytest

from radares_anunciados.roads import direction as d
from radares_anunciados.roads.direction import Anchor, Anchors, Signal, decide
from radares_anunciados.roads.graph import segment_bearing
from radares_anunciados.roads.pbf import _CODE, Network
from radares_anunciados.roads.snap import snap

ORIGIN = (40.0, -3.0)
A2 = frozenset({"A-2"})


def xy(x_m, y_m):
    lat = ORIGIN[0] + y_m / 111_195
    lon = ORIGIN[1] + x_m / (111_195 * math.cos(math.radians(ORIGIN[0])))
    return (lat, lon)


def network(*ways):
    """Each way: (list of (x, y), ref, oneway); vertices every 250 m on straight pieces."""
    net = Network()
    ids: dict[tuple[float, float], int] = {}
    for pts, ref, oneway in ways:
        dense = [pts[0]]
        for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
            n = max(1, round(math.hypot(bx - ax, by - ay) / 250))
            dense += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(1, n + 1)]
        nodes = [ids.setdefault(p, len(ids) + 1) for p in dense]
        refs = frozenset({ref}) if ref else frozenset()
        net.add_way(nodes, [xy(*p) for p in dense], _CODE["primary"], refs, oneway)
    return net


def straight_a2():
    """The A-2, two-way, drawn south to north (node order: north is side 1)."""
    return network(([(0, -30_000), (0, 30_000)], "A-2", 0))


def s1(net, anchors, km=100.0, km_direction="positive"):
    at = snap(net, xy(0, 0), A2)
    return d.official(net, at, km, km_direction, A2, Anchors(net, anchors))


# --- S1, official km ------------------------------------------------------------


def test_s1_names_the_side_where_the_km_grow():
    net = straight_a2()
    ahead = [Anchor("k105", xy(0, 5000), 105.0, A2)]
    found = s1(net, ahead)
    assert (found.side, found.anchor) == (1, "k105")
    assert found.walked_m == pytest.approx(5000, abs=5)
    assert s1(net, ahead, km_direction="negative").side == -1
    behind = [Anchor("k95", xy(0, 5000), 95.0, A2)]  # km fall to the north
    assert s1(net, behind).side == -1


def test_s1_is_unresolved_when_two_valid_anchors_disagree():
    net = straight_a2()
    anchors = [Anchor("k105", xy(0, 5000), 105.0, A2), Anchor("k97", xy(0, 3000), 97.0, A2)]
    found = s1(net, anchors)
    assert found.side is None and "disagree" in found.note


def test_s1_discards_an_anchor_from_another_km_sequence():
    """A-2 in the DGT file: km 122.0 and 136.3 lie 22.1 km apart along the road."""
    assert not d.anchor_valid(22_100, 14.3)
    assert d.anchor_valid(14_500, 14.3)  # positive control
    assert not d.anchor_valid(400, 0.4)  # too close to say anything
    net = straight_a2()
    far = [Anchor("other-sequence", xy(0, 22_100), 114.3, A2)]
    assert s1(net, far).side is None
    near = [Anchor("same-sequence", xy(0, 14_500), 114.3, A2)]
    assert s1(net, near).side == 1


def test_s1_needs_the_anchor_on_the_same_road():
    net = network(([(0, -30_000), (0, 30_000)], "A-2", 0), ([(500, 0), (500, 9000)], "N-1", 0))
    other_road = [Anchor("n1", xy(500, 5000), 105.0, frozenset({"N-1"}))]
    assert s1(net, other_road).side is None
    milestone = [Anchor("osm-node-1", xy(10, 5000), 105.0, None, d.MILESTONE_SNAP_M)]
    assert s1(net, milestone).side == 1  # a milestone counts on whatever road it stands


def test_s1_says_nothing_without_an_official_direction():
    net = straight_a2()
    anchors = [Anchor("k105", xy(0, 5000), 105.0, A2)]
    assert s1(net, anchors, km_direction="both") is None
    assert s1(net, anchors, km_direction=None) is None


# --- S2, carriageway -------------------------------------------------------------------


def dual():
    """Two one-way carriageways of the A-2, 25 m apart: x=0 drives north, x=25 south."""
    return network(([(0, -5000), (0, 5000)], "A-2", 1), ([(25, 5000), (25, -5000)], "A-2", 1))


@pytest.mark.parametrize("x, travel", [(9.9, 0), (15.1, 180), (10.5, None), (14.5, None)])
def test_s2_takes_the_clearly_nearer_carriageway(x, travel):
    net = dual()
    p = xy(x, 0)
    at = snap(net, p, A2)
    found = d.carriageway(net, at, p)
    # 9.9 m from one, 15.1 m from the other: over 5 m clear; 10.5 / 14.5: 4 m, no signal
    if travel is None:
        assert found.side is None and "between" in found.note
    else:
        assert segment_bearing(net, at.way, at.seg, found.side) == travel


def test_s2_needs_the_other_carriageway_running_the_other_way():
    net = network(([(0, -5000), (0, 5000)], "A-2", 1), ([(25, -5000), (25, 5000)], "A-2", 1))
    assert d.carriageway(net, snap(net, xy(5, 0), A2), xy(5, 0)) is None
    two_way = straight_a2()
    assert d.carriageway(two_way, snap(two_way, xy(5, 0), A2), xy(5, 0)) is None


# --- S3, the OSM tag --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag, side",
    [("0", 1), ("25", 1), ("180", -1), ("200", -1), ("forward", 1), ("backward", -1), ("90", None)],
)
def test_s3_reads_the_tag_against_the_ways_node_order(tag, side):
    net = straight_a2()  # node order runs north
    found = d.osm_tag(net, snap(net, xy(0, 0)), tag)
    assert found.side == side


def test_s3_ignores_place_names():
    net = straight_a2()
    assert d.osm_tag(net, snap(net, xy(0, 0)), "ZARAGOZA") is None


# --- the decision ------------------------------------------------------------------------------


def sig(side):
    return Signal(side)


def test_any_two_opposing_signals_publish_nothing():
    assert decide(sig(1), None, sig(-1), (1, -1)).side is None
    assert decide(None, sig(1), sig(-1), (1,)).side is None


def test_only_official_and_carriageway_together_are_high_confidence():
    both = decide(sig(1), sig(1), None, (1,))
    assert (both.side, both.source, both.confidence) == (1, "official_km", "high")
    assert decide(sig(1), sig(1), sig(1), (1,)).confidence == "high"
    assert decide(sig(1), None, None, (1, -1)).confidence == "low"
    assert decide(sig(1), None, sig(1), (1, -1)).confidence == "low"
    alone = decide(None, sig(1), sig(1), (1,))
    assert (alone.source, alone.confidence) == ("oneway", "low")
    assert decide(None, None, sig(-1), (1, -1)).source == "osm_tag"


def test_official_beats_the_nearest_carriageway_with_low_confidence():
    found = decide(sig(-1), sig(1), None, (1,))
    assert (found.side, found.source, found.confidence, found.resnap) == (
        -1,
        "official_km",
        "low",
        True,
    )


def test_an_osm_tag_against_a_one_way_road_is_a_conflict():
    assert decide(None, None, sig(-1), (1,)).side is None
    assert decide(None, None, sig(1), (1,)).side == 1


def test_no_signal_no_bearing():
    assert decide(None, None, None, (1, -1)).side is None
    assert decide(Signal(None, "no km anchor"), None, None, (1, -1)).side is None
