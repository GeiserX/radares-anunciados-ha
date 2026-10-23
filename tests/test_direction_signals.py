"""The road, km and monitored direction the publishers give, kept in the feed."""

import json
from dataclasses import fields
from pathlib import Path

from radares_anunciados import feed, store
from radares_anunciados.model import Radar
from radares_anunciados.sources import dgt, navarra, sct

FIX = Path(__file__).parent / "fixtures"
DGT = (FIX / "dgt_radares.xml").read_bytes()
DGT_NAVARRA = (FIX / "dgt_navarra_2026-10-01.xml").read_bytes()
NAVARRA_API = (FIX / "navarra_radars_api_2026-10-01.json").read_bytes()
NEW_KEYS = ("road_ref", "km", "km_direction")


def by_id(radars):
    return {r.id: r for r in radars}


def test_the_dgt_fixture_carries_directionRelative():
    # 7 positive and 2 negative in the file: on fixed radars and on section ends
    assert DGT.count(b"directionRelative>positive<") == 7
    assert DGT.count(b"directionRelative>negative<") == 2


def test_dgt_fixed_radars_keep_road_km_and_km_direction():
    fixed = by_id(r for r in dgt.parse(DGT, None) if r.kind == "fixed")
    got = {i: (r.road_ref, r.km, r.km_direction, r.direction) for i, r in fixed.items()}
    assert got == {
        "dgt-CABINACINEMOMETRO_120001": ("A-2", 202.33, "positive", "ZARAGOZA"),
        "dgt-CABINACINEMOMETRO_120163": ("A-30", 190.453, "positive", None),
        "dgt-CABINACINEMOMETRO_120164": ("RM-19", 17.799, "positive", None),
        "dgt-CABINACINEMOMETRO_120154": ("N-332", 141.002, None, None),  # none in the file
    }
    # the name, built from the same reference point, is as before
    assert (
        fixed["dgt-CABINACINEMOMETRO_120001"].name == "Radar fijo A-2 km 202.3 (sentido ZARAGOZA)"
    )


def test_dgt_section_ends_carry_none_of_the_new_fields():
    # a section is treated as undirected: its two ends disagree in some entries
    for r in dgt.parse(DGT, None):
        if r.kind == "section":
            assert (r.road_ref, r.km, r.km_direction) == (None, None, None)


def test_dgt_and_navarra_agree_on_the_direction_of_the_same_radars():
    official = dgt.parse(DGT_NAVARRA, {"31"})
    regional = navarra.parse_api(NAVARRA_API)
    pairs = 0
    for o in official:
        for n in regional:
            if n.road_ref == o.road_ref and abs(n.km - o.km) < 0.01:
                assert n.km_direction == o.km_direction, (o.id, n.id)
                pairs += 1
    assert pairs == 7
    assert {r.km_direction for r in official} == {"positive", "negative"}


def test_navarra_maps_creciente_to_positive_and_decreciente_to_negative():
    radars = by_id(navarra.parse_api(NAVARRA_API))
    a1 = radars["navarra-A-1-401.5-C"]
    assert (a1.road_ref, a1.km, a1.km_direction, a1.direction) == (
        "A-1",
        401.561,
        "positive",
        "creciente",
    )
    a15 = radars["navarra-A-15-127.6-D"]
    assert (a15.km_direction, a15.direction) == ("negative", "decreciente")


def test_sct_gives_the_road_number_and_a_single_km_only():
    radars = by_id(sct.parse_fixed((FIX / "sct_radars.txt").read_text("latin-1")))
    assert (radars["sct-C-32nord-85"].road_ref, radars["sct-C-32nord-85"].km) == ("C-32", 85.0)
    assert radars["sct-A-2-445.35"].km == 445.35
    section = radars["sct-N-230-150.5-156"]
    assert (section.road_ref, section.km, section.km_direction) == ("N-230", None, None)
    assert all(r.km_direction is None for r in radars.values())  # the file gives none


def test_to_geojson_writes_the_keys_only_when_known():
    radars = dgt.parse(DGT, None)
    doc = json.loads(feed.to_geojson(radars))
    props = {f["id"]: f["properties"] for f in doc["features"]}
    a2 = props["dgt-CABINACINEMOMETRO_120001"]
    assert (a2["road_ref"], a2["km"], a2["km_direction"]) == ("A-2", 202.33, "positive")
    n332 = props["dgt-CABINACINEMOMETRO_120154"]
    assert (n332["road_ref"], n332["km"]) == ("N-332", 141.002)
    assert "km_direction" not in n332  # omitted, never null
    for p in props.values():
        assert all(p[k] is not None for k in NEW_KEYS if k in p)
    section = props["dgt-CVM_161274-from"]
    assert not set(NEW_KEYS) & set(section)
    # the existing keys are untouched: direction is still the source's text
    assert a2["direction"] == "ZARAGOZA" and section["direction"] == "MADRID"


def test_a_radar_without_the_new_fields_is_written_as_before():
    plain = Radar(id="x", source="osm", kind="fixed", name="Radar", lat=1.5, lon=2.5, radius_m=200)
    (feature,) = json.loads(feed.to_geojson([plain]))["features"]
    assert list(feature["properties"]) == [
        "name",
        "source",
        "kind",
        "radius_m",
        "active",
        "valid_from",
        "valid_to",
        "reported",
        "maxspeed",
        "direction",
        "province",
        "url",
        "attribution",
    ]


def test_the_cache_keeps_the_new_fields_and_reads_an_old_record():
    r = next(r for r in dgt.parse(DGT, None) if r.km_direction)
    assert store.radar_from_json(store.radar_to_json(r)) == r
    old = {k: v for k, v in store.radar_to_json(r).items() if k not in NEW_KEYS}
    back = store.radar_from_json(old)
    assert (back.road_ref, back.km, back.km_direction) == (None, None, None)
    assert {f.name for f in fields(Radar)} >= set(NEW_KEYS)


def test_home_assistant_zones_ignore_the_new_fields():
    from dataclasses import replace

    from radares_anunciados import ha

    radars = dgt.parse(DGT_NAVARRA, None) + navarra.parse_api(NAVARRA_API)
    bare = [replace(r, road_ref=None, km=None, km_direction=None) for r in radars]
    assert bare != radars
    assert ha.plan([], ha.zoned(radars)) == ha.plan([], ha.zoned(bare))


def test_a_direction_outside_the_contract_is_left_out():
    """DATEX allows more values than positive, negative and both; only those three
    reach the feed, and `direction` does not change."""
    odd = DGT.replace(b"directionRelative>positive<", b"directionRelative>unknown<")
    assert odd != DGT
    before = by_id(r for r in dgt.parse(DGT, None) if r.kind == "fixed")
    after = by_id(r for r in dgt.parse(odd, None) if r.kind == "fixed")
    assert before["dgt-CABINACINEMOMETRO_120001"].km_direction == "positive"
    assert after["dgt-CABINACINEMOMETRO_120001"].km_direction is None
    assert {r.km_direction for r in after.values()} == {None}
    assert {i: r.direction for i, r in after.items()} == {i: r.direction for i, r in before.items()}
