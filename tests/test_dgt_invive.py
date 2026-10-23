import json
import logging
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from radares_anunciados import cli, feed, ha, net, sources, speed
from radares_anunciados.geo import densify, distance_m
from radares_anunciados.model import Radar
from radares_anunciados.sources import Context, dgt_invive
from radares_anunciados.speed import Radius

FIX = Path(__file__).parent / "fixtures"
XML = (FIX / "dgt_tramos_invive.xml").read_bytes()
# The Overpass answer for Murcia's INVIVE roads, trimmed to RM-D11, RM-F36 and
# five N-332 ways far from the N-332 stretch (as in the full answer).
OVERPASS = (FIX / "overpass_invive_murcia.json").read_bytes()
MURCIA = frozenset({"30"})

# Every province name in the full file of 2026-10-01 (43 provinces, 1,331 stretches).
FILE_PROVINCES = [
    "A CORUÑA", "ALACANT/ALICANTE", "ALBACETE", "ALMERÍA", "ASTURIAS", "BADAJOZ", "BURGOS",
    "CANTABRIA", "CASTELLÓ/CASTELLÓN", "CIUDAD REAL", "CUENCA", "CÁCERES", "CÁDIZ", "CÓRDOBA",
    "GRANADA", "GUADALAJARA", "HUELVA", "HUESCA", "ILLES BALEARS", "JAÉN", "LA RIOJA",
    "LAS PALMAS", "LEÓN", "LUGO", "MADRID", "MURCIA", "MÁLAGA", "NAVARRA", "OURENSE", "PALENCIA",
    "PONTEVEDRA", "SALAMANCA", "SANTA CRUZ DE TENERIFE", "SEGOVIA", "SEVILLA", "SORIA", "TERUEL",
    "TOLEDO", "VALLADOLID", "VALÈNCIA/VALENCIA", "ZAMORA", "ZARAGOZA", "ÁVILA",
]  # fmt: skip


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("RADARES_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RADARES_CACHE", str(tmp_path))


def ctx(provinces=MURCIA, zones=True) -> Context:
    return Context(
        day=date(2026, 10, 1), provinces=provinces, boxes=(), radius=Radius(), stretch_zones=zones
    )


def by_road(stretches, road, km_from):
    return next(s for s in stretches if s.road == road and s.km_from == km_from)


def test_parses_the_file_with_its_malformed_xmlns():
    assert b'xmlns:xsd="http:www.w3.org/2001/XMLSchema"' in XML  # kept as served
    stretches = dgt_invive.parse(XML)
    assert len(stretches) == 6
    first = next(s for s in stretches if s.id == "dgt_invive-Tramo_Invive_1")
    assert first.name == "Tramo de radar móvil CM-220 km 17.3-34.8"
    assert (first.road, first.province, first.km_from, first.km_to) == ("CM-220", "02", 17.3, 34.8)
    assert first.start == (39.25851, -1.91995) and first.end == (39.11986, -1.99897)
    assert first.direction == "both"
    assert first.maxspeed is None  # the file publishes no limit
    assert first.url == dgt_invive.URL
    assert first.attribution == dgt_invive.ATTRIBUTION
    assert first.line is None


def test_keeps_only_the_selected_provinces():
    assert {s.province for s in dgt_invive.parse(XML, MURCIA)} == {"30"}
    assert len(dgt_invive.parse(XML, MURCIA)) == 4
    assert [s.province for s in dgt_invive.parse(XML, {"28"})] == ["28"]
    assert dgt_invive.parse(XML, {"08"}) == []


def test_every_province_name_in_the_file_has_its_ine_code():
    codes = [dgt_invive.province_code(n) for n in FILE_PROVINCES]
    assert None not in codes
    assert len(set(codes)) == 43
    assert set(codes) <= dgt_invive.COVERED
    assert dgt_invive.province_code("MURCIA") == "30"
    assert dgt_invive.province_code("VALÈNCIA/VALENCIA") == "46"
    assert dgt_invive.province_code("ALACANT/ALICANTE") == "03"
    assert dgt_invive.province_code("ATLANTIS") is None


def test_a_stretch_in_an_unknown_province_is_skipped_not_guessed(caplog):
    xml = XML.replace(b">ALBACETE<", b">ATLANTIS<")
    with caplog.at_level(logging.WARNING):
        stretches = dgt_invive.parse(xml)
    assert len(stretches) == 5
    assert "Tramo_Invive_1" not in {s.id.removeprefix("dgt_invive-") for s in stretches}
    assert "ATLANTIS" in caplog.text


def test_geometry_query_is_one_ref_alternation_in_the_province_box():
    query = dgt_invive.geometry_query((37.37, -2.35, 38.76, -0.64), ["RM-D11", "N-301a", "x;out;"])
    assert "[bbox:37.37,-2.35,38.76,-0.64]" in query
    assert '"ref"~"^(N-301a|RM-D11)(;|$)|;(N-301a|RM-D11)(;|$)",i' in query
    assert "x;out" not in query  # only road refs reach Overpass
    assert "name" not in query and "area" not in query
    assert query.endswith("out geom qt;")


def test_every_ref_shape_in_the_file_is_looked_up_and_nothing_else():
    # Refs of the live file of 2026-10-01 that a single shape used to drop.
    odd = ["N-I", "N-IIa", "N-Va", "N-121-A", "ZA-P-1405", "A-403R1", "EX-A2-R1", "CG-2.1"]
    query = dgt_invive.geometry_query((40, -5, 41, -4), [*odd, 'A-1"];out;', "A-1\\", "(.*)"])
    alt = query.split('"ref"~"^(')[1].split(")(;|$)")[0]
    assert alt.split("|") == sorted([*odd[:-1], "CG-2[.]1"], key=str.upper)
    assert dgt_invive.geometry_query((40, -5, 41, -4), iter(['"', "*"])) is None


def test_a_ref_left_out_of_the_query_is_logged(caplog):
    with caplog.at_level(logging.WARNING):
        dgt_invive.geometry_query((40, -5, 41, -4), (r for r in ["N-332", 'A-1"']))
    assert "'A-1\"' not looked up" in caplog.text


def test_follows_the_road_between_the_end_points():
    stretches = dgt_invive.follow(dgt_invive.parse(XML, MURCIA), OVERPASS)
    d11 = by_road(stretches, "RM-D11", 0.0)
    assert d11.line is not None
    assert d11.line[0] == d11.start and d11.line[-1] == d11.end
    # the path along OpenStreetMap matches the published km range within 5 %
    length = dgt_invive._length(list(d11.line))
    assert abs(length - 10_390) < 0.05 * 10_390
    assert "OpenStreetMap" in d11.attribution
    assert not d11.name.endswith(dgt_invive.STRAIGHT_NOTE)
    # RM-F36 km 0: the published start is about 1 km off the mapped road; still followed
    assert by_road(stretches, "RM-F36", 0.0).line is not None
    assert by_road(stretches, "RM-F36", 5.82).line is not None


def test_a_road_not_found_stays_a_straight_line_and_says_so():
    stretches = dgt_invive.follow(dgt_invive.parse(XML, MURCIA), OVERPASS)
    n332 = by_road(stretches, "N-332", 0.0)  # the mapped N-332 is km away from these points
    assert n332.line is None
    assert n332.name == "Tramo de radar móvil N-332 km 0-13.07" + dgt_invive.STRAIGHT_NOTE
    assert "OpenStreetMap" not in n332.attribution
    feature = json.loads(feed.to_geojson([], [n332]))["features"][0]
    assert feature["geometry"]["coordinates"] == [
        [n332.start[1], n332.start[0]],
        [n332.end[1], n332.end[0]],
    ]
    assert "línea recta" in feature["properties"]["name"]


def test_without_bridges_a_ref_broken_at_a_roundabout_is_not_followed(monkeypatch):
    # RM-D11's ref stops at a roundabout; only the bridge between way ends joins it.
    monkeypatch.setattr(dgt_invive, "BRIDGE_M", 0)
    stretches = dgt_invive.follow(dgt_invive.parse(XML, MURCIA), OVERPASS)
    assert by_road(stretches, "RM-D11", 0.0).line is None


def test_a_path_far_longer_or_far_shorter_than_the_published_km_is_refused():
    d11 = by_road(dgt_invive.parse(XML, MURCIA), "RM-D11", 0.0)
    line = dgt_invive.route(dgt_invive.road_ways(OVERPASS, "RM-D11"), d11.start, d11.end)
    assert dgt_invive.plausible(line, d11)
    assert not dgt_invive.plausible(line, replace(d11, km_to=2.0))
    assert not dgt_invive.plausible(line, replace(d11, km_to=40.0))


def stretch_like(start, end, km_from, km_to):
    """A stretch nobody can follow (its road is in no answer), as published."""
    base = by_road(dgt_invive.parse(XML, MURCIA), "RM-D11", 0.0)
    s = replace(base, road="N-Va", start=start, end=end, km_from=km_from, km_to=km_to)
    return dgt_invive.follow([s], OVERPASS)[0]


def test_end_points_far_apart_for_a_short_stretch_get_no_zones():
    # Tramo_Invive_1205 on 2026-10-01: N-Va km 148-148.15, end points 20 km apart.
    s = stretch_like((40.06362, -5.33328), (39.92485, -5.18397), 148.0, 148.15)
    assert distance_m(s.start, s.end) > 19_000
    assert s.line is None and s.name.endswith(dgt_invive.MISMATCH_NOTE)
    assert dgt_invive.zones(s, ctx()) == []


def test_one_point_given_as_both_ends_of_a_long_stretch_gets_no_zones():
    # Tramo_Invive_46 on 2026-10-01: N-332 km 42.26-53.89, start == end.
    s = stretch_like((37.95732, -0.70904), (37.95732, -0.70904), 42.26, 53.89)
    assert s.name.endswith(dgt_invive.MISMATCH_NOTE)
    assert dgt_invive.zones(s, ctx()) == []
    # a short stretch given as one point is still one circle: it covers it
    short = stretch_like((37.95732, -0.70904), (37.95732, -0.70904), 1.0, 2.0)
    assert short.name.endswith(dgt_invive.STRAIGHT_NOTE)
    assert len(dgt_invive.zones(short, ctx())) == 1
    # a winding road whose ends are close (LP-1 km 0-102.43, ends 16.8 km apart)
    loop = stretch_like((28.68, -17.76), (28.6, -17.92), 0.0, 102.43)
    assert loop.name.endswith(dgt_invive.STRAIGHT_NOTE)
    assert len(dgt_invive.zones(loop, ctx())) == 2


def test_a_road_not_followed_gets_zones_only_at_its_end_points_named_so():
    n332 = by_road(dgt_invive.follow(dgt_invive.parse(XML, MURCIA), OVERPASS), "N-332", 0.0)
    radars = dgt_invive.zones(n332, ctx())
    assert [(r.lat, r.lon) for r in radars] == [n332.start, n332.end]
    assert {r.name for r in radars} == {"Radar móvil N-332 km 0-13.07" + dgt_invive.ENDS_NOTE}
    assert len({r.id for r in radars}) == 2


def test_along_puts_points_at_both_ends_and_never_further_apart_than_asked():
    line = [(37.96, -1.09), (37.97, -1.10), (37.99, -1.10)]
    points = dgt_invive.along(line, 1000)
    assert points[0] == line[0]
    assert distance_m(points[-1], line[-1]) < 1
    gaps = [distance_m(a, b) for a, b in zip(points, points[1:], strict=False)]
    assert max(gaps) <= 1000 + 1
    assert dgt_invive.along([(37.0, -1.0), (37.0, -1.0)], 1000) == [(37.0, -1.0)]


def test_zones_cover_the_whole_road_with_the_speed_rule_radius():
    d11 = by_road(dgt_invive.follow(dgt_invive.parse(XML, MURCIA), OVERPASS), "RM-D11", 0.0)
    radars = dgt_invive.zones(d11, ctx())
    radius = speed.auto_radius(speed.ROAD_KMH, speed.POINT_LEAD_S)  # no published limit: 90
    assert {r.radius_m for r in radars} == {radius}
    assert {r.name for r in radars} == {"Radar móvil RM-D11 km 0-10.39"}
    assert {r.kind for r in radars} == {"mobile_stretch"}
    assert len({r.id for r in radars}) == len(radars)
    assert (radars[0].lat, radars[0].lon) == d11.start
    assert all(r.province == "30" and r.direction == "both" and r.url for r in radars)
    for point in densify(list(d11.line), 50):
        assert min(distance_m(point, (r.lat, r.lon)) for r in radars) <= 0.9 * radius + 1
    # the core resizes point radars by the same rule, so the spacing still holds
    assert speed.size(radars, Radius()) == radars


def test_zones_are_off_by_default():
    result = dgt_invive.build(XML, ctx(), with_zones=False, max_age_s=1)
    assert result.radars == [] and len(result.stretches) == 4


def test_zones_need_a_province_list(caplog):
    with caplog.at_level(logging.WARNING):
        result = dgt_invive.build(XML, ctx(provinces=None), with_zones=True, max_age_s=1)
    assert result.radars == [] and len(result.stretches) == 6
    assert "province list" in caplog.text


def fake_network(monkeypatch, answer):
    """NAP serves the fixture; Overpass answers with ``answer()``. Returns the
    Overpass calls as (data, timeout)."""
    calls = []

    def cached_get(url, data=None, headers=None, max_age_s=0):
        assert url == dgt_invive.URL
        return XML

    def get(url, data=None, headers=None, timeout=90, tries=3):
        calls.append((data, timeout))
        return answer()

    monkeypatch.setattr(net, "cached_get", cached_get)
    monkeypatch.setattr(net, "get", get)
    return calls


def test_fetch_with_zones_on(monkeypatch):
    calls = fake_network(monkeypatch, lambda: OVERPASS)
    result = dgt_invive.fetch(ctx(frozenset({"30", "28"})))
    assert {r.province for r in result.radars} == {"30", "28"}
    assert all(r.name.startswith("Radar") for r in result.radars)
    assert len(calls) == 2  # one query per selected province
    assert "[bbox:37.37,-2.35,38.76,-0.64]" in calls[1][0]["data"]
    # the client waits longer than the query may run on the server
    assert {c[1] for c in calls} == {dgt_invive.OVERPASS_WAIT_S}
    assert f"[timeout:{dgt_invive.OVERPASS_TIMEOUT_S}]" in calls[0][0]["data"]
    assert dgt_invive.OVERPASS_WAIT_S > dgt_invive.OVERPASS_TIMEOUT_S
    dgt_invive.fetch(ctx(frozenset({"30", "28"})))
    assert len(calls) == 2  # a good answer is cached


def test_overpass_down_keeps_the_last_good_zones(monkeypatch):
    up = [True]

    def answer():
        if up[0]:
            return OVERPASS
        raise OSError("HTTP Error 504: Gateway Timeout")

    fake_network(monkeypatch, answer)
    first = sources.run(dgt_invive.SOURCE, ctx(), now=1_000)
    assert first.up and first.result.radars
    monkeypatch.setattr(dgt_invive, "GEOMETRY_MAX_AGE_S", 0)  # the geometry copy expired
    up[0] = False
    with pytest.raises(OSError):
        dgt_invive.fetch(ctx())
    second = sources.run(dgt_invive.SOURCE, ctx(), now=2_000)
    assert not second.up and "504" in second.error
    assert second.result.radars == first.result.radars  # no zone moves onto a straight line
    assert second.result.stretches == first.result.stretches


TIMED_OUT = json.dumps(
    {
        "version": 0.6,
        "elements": [],
        "remark": 'runtime error: Query timed out in "query" at line 1 after 181 seconds.',
    }
).encode()


@pytest.mark.parametrize("payload", [TIMED_OUT, b'{"version": 0.6, "elements": []}'])
def test_an_incomplete_overpass_answer_fails_and_is_not_cached(monkeypatch, payload):
    calls = fake_network(monkeypatch, lambda: payload)
    for _ in range(2):
        with pytest.raises(dgt_invive.GeometryError):
            dgt_invive.fetch(ctx())
    assert len(calls) == 2  # asked again: the bad answer was not kept


def test_a_wrong_zones_setting_is_refused(monkeypatch):
    monkeypatch.setenv("RADARES_STRETCH_ZONES", "yes please")
    with pytest.raises(ValueError, match="RADARES_STRETCH_ZONES"):
        cli.stretch_zones()
    monkeypatch.setenv("RADARES_STRETCH_ZONES", "ON")
    assert cli.stretch_zones()
    monkeypatch.delenv("RADARES_STRETCH_ZONES")
    assert not cli.stretch_zones()  # off by default


def test_the_zones_setting_comes_from_the_context_and_splits_the_last_good_result(monkeypatch):
    # Zones off after a run with zones on: a failed download must not bring the
    # zones back from the last good result, so the setting is in the fingerprint.
    fake_network(monkeypatch, lambda: OVERPASS)
    monkeypatch.setenv("RADARES_STRETCH_ZONES", "on")  # the source reads the context only
    assert not dgt_invive.fetch(ctx(zones=False)).radars
    assert dgt_invive.fetch(ctx(zones=True)).radars
    assert sources.fingerprint(dgt_invive.SOURCE, ctx(zones=False)) != sources.fingerprint(
        dgt_invive.SOURCE, ctx(zones=True)
    )
    monkeypatch.setenv("RADARES_PROVINCES", "30")
    assert cli.context(date(2026, 10, 1)).stretch_zones


def test_stretch_zones_rank_below_fixed_radars_under_the_cap():
    d11 = by_road(dgt_invive.follow(dgt_invive.parse(XML, MURCIA), OVERPASS), "RM-D11", 0.0)
    stretch_zones = dgt_invive.zones(d11, ctx())
    home = d11.start
    fixed = [
        Radar(
            id=f"dgt-{i}", source="dgt", kind="fixed", name=f"Radar fijo {i}",
            lat=38.5 + i / 100, lon=-1.0, radius_m=1200,
        )
        for i in range(5)
    ]  # fmt: skip
    kept, left_out = ha.select(stretch_zones + fixed, 5, home)
    assert {r.kind for r in kept} == {"fixed"} and left_out == len(stretch_zones)


def test_a_limit_lookup_does_not_shrink_the_circles_along_a_stretch():
    d11 = by_road(dgt_invive.follow(dgt_invive.parse(XML, MURCIA), OVERPASS), "RM-D11", 0.0)
    radars = [replace(r, maxspeed=50) for r in dgt_invive.zones(d11, ctx())]
    assert {r.radius_m for r in speed.size(radars, Radius())} == {radars[0].radius_m}


# --- end points that cannot be the published corridor ----------------------------


# Tramo_Invive_1 is CM-220 km 17.3-34.8 with its ends 17 km apart.
def _at(lat: bytes, lon: bytes) -> bytes:
    return b"<_0:latitude>" + lat + b"</_0:latitude>\n" + b" " * 16 + b"<_0:longitude>" + lon


TO_POINT = _at(b"39.11986", b"-1.99897")


def _first(xml: bytes) -> list[str]:
    return [s.id for s in dgt_invive.parse(xml) if s.id == "dgt_invive-Tramo_Invive_1"]


def test_a_corridor_given_as_one_point_for_a_long_km_range_is_dropped(caplog):
    # Tramo_Invive_46 on 2026-10-23: N-332 km 42.26-53.89, start == end.
    assert TO_POINT in XML
    one_point = XML.replace(
        TO_POINT,
        _at(b"39.25851", b"-1.91995"),
    )
    with caplog.at_level(logging.WARNING):
        assert _first(one_point) == []
    assert "Tramo_Invive_1" in caplog.text and "0 m apart" in caplog.text
    assert "dropped" in caplog.text
    assert _first(XML) == ["dgt_invive-Tramo_Invive_1"]  # the published ends: kept


def test_ends_far_apart_for_a_short_km_range_are_dropped(caplog):
    # Tramo_Invive_1205 on 2026-10-23: N-Va km 148.0-148.15, ends 20 km apart.
    short = XML.replace(b">34800.0<", b">17450.0<")
    assert short != XML
    with caplog.at_level(logging.WARNING):
        assert _first(short) == []
    assert "km 17.3-17.45" in caplog.text
    # the same 150 m range with ends 110 m apart is a corridor: kept
    near = short.replace(
        TO_POINT,
        _at(b"39.25950", b"-1.91995"),
    )
    assert _first(near) == ["dgt_invive-Tramo_Invive_1"]
