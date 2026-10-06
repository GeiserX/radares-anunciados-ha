import json
import os
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from radares_anunciados import cli, feed, metrics, net, provinces, sources, store
from radares_anunciados.geo import distance_m
from radares_anunciados.model import SPAIN, Radar, SourceResult, Stretch, WeeklyList
from radares_anunciados.sources import Context, Source, dgt, murcia, osm
from radares_anunciados.speed import Radius

FIX = Path(__file__).parent / "fixtures"
MONDAY = date(2026, 9, 28)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("RADARES_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RADARES_CACHE", str(tmp_path))


def ctx(**kw) -> Context:
    base = Context(
        day=MONDAY, provinces=frozenset({"30"}), boxes=(osm.MURCIA_REGION,), radius=Radius()
    )
    return replace(base, **kw)


def test_every_source_is_registered_with_its_terms():
    assert list(sources.REGISTRY) == [
        "dgt",
        "osm",
        "osm_notes",
        "murcia",
        "sct",
        "sct_remolc",
        "dgt_invive",
        "euskadi",
        "navarra",
        "donostia",
        "donostia_movil",
        "madrid",
        "salamanca",
        "leon",
        "barcelona_multas",
        "madrid_multas",
    ]
    for key, source in sources.REGISTRY.items():
        assert source.key == key
        assert callable(source.fetch)
        assert source.attribution and source.licence
        assert source.max_age_s > 0
    assert sources.REGISTRY["murcia"].provinces == {"30"}
    assert sources.REGISTRY["dgt"].provinces is None


def test_selected_sources():
    keys = lambda found: [s.key for s in found]  # noqa: E731
    assert keys(sources.selected(None, frozenset({"30"}))) == ["dgt", "osm", "murcia", "dgt_invive"]
    assert keys(sources.selected(["osm", "dgt"], None)) == ["dgt", "osm"]
    # a city list outside the selected provinces is not fetched
    assert keys(sources.selected(None, frozenset({"28"}))) == [
        "dgt",
        "osm",
        "dgt_invive",
        "madrid",
        "madrid_multas",
    ]
    # DGT runs no mobile-radar stretches in Catalonia
    assert keys(sources.selected(None, frozenset({"08"}))) == [
        "dgt",
        "osm",
        "sct",
        "sct_remolc",
        "barcelona_multas",
    ]
    assert keys(sources.selected(None, None)) == [k for k in sources.REGISTRY if k != "osm_notes"]
    with pytest.raises(ValueError, match="nope"):
        sources.selected(["dgt", "nope"], None)


@pytest.mark.parametrize(
    ("env", "codes"),
    [
        ({}, {"30"}),  # today's default
        ({"RADARES_DGT_PROVINCES": "3,46"}, {"03", "46"}),  # the old name still works
        ({"RADARES_PROVINCES": "28", "RADARES_DGT_PROVINCES": "30"}, {"28"}),  # the new one wins
        ({"RADARES_PROVINCES": "all"}, None),
    ],
)
def test_province_selection(monkeypatch, env, codes):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert cli.selected_provinces() == (None if codes is None else frozenset(codes))


def test_an_unknown_province_is_refused():
    with pytest.raises(ValueError, match="99"):
        provinces.parse("30,99")


def test_osm_default_area_is_unchanged_for_murcia():
    assert cli.osm_boxes(frozenset({"30"})) == (osm.MURCIA_REGION,)
    assert osm.query_boxes([osm.MURCIA_REGION]) == osm.query(osm.MURCIA_REGION)


def test_osm_bbox_setting_keeps_working(monkeypatch):
    monkeypatch.setenv("RADARES_OSM_BBOX", "37.5,-1.5,38.0,-1.0")
    assert cli.osm_boxes(frozenset({"30"})) == ((37.5, -1.5, 38.0, -1.0),)
    monkeypatch.setenv("RADARES_OSM_BBOX", "1,2,3")
    with pytest.raises(ValueError):
        cli.osm_boxes(None)


@pytest.mark.parametrize(
    ("place", "point"),
    [
        ("Madrid", (40.4168, -3.7038)),
        ("Finisterre", (42.9076, -9.2650)),
        ("Cap de Creus", (42.3190, 3.3170)),
        ("Maó", (39.8885, 4.2658)),
        ("Formentera", (38.6650, 1.5000)),
        ("El Hierro", (27.7300, -18.0300)),
        ("Lanzarote", (29.2100, -13.5000)),
        ("Tarifa", (36.0130, -5.6060)),
        ("Ceuta", (35.8890, -5.3200)),
        ("Melilla", (35.2930, -2.9380)),
    ],
)
def test_osm_all_covers_spain_without_an_area_lookup(monkeypatch, place, point):
    monkeypatch.setenv("RADARES_OSM_BBOX", "all")
    boxes = cli.osm_boxes(frozenset({"30"}))
    assert len(boxes) == 52
    assert any(s <= point[0] <= n and w <= point[1] <= e for s, w, n, e in boxes), place
    q = osm.query_boxes(boxes)
    assert "area" not in q and q.count('node["highway"="speed_camera"]') == 52


def test_dgt_radars_and_sections_carry_province_direction_and_km():
    result = dgt.parse_all((FIX / "dgt_radares.xml").read_bytes(), None)
    assert {r.province for r in result.radars} == {"03", "30", "50"}
    cabin = next(r for r in result.radars if r.id == "dgt-CABINACINEMOMETRO_120001")
    assert (cabin.direction, cabin.province) == ("ZARAGOZA", "50")
    murcia_sections = [s for s in result.stretches if s.province == "30"]
    assert len(murcia_sections) == 2
    zaragoza = next(s for s in result.stretches if s.id == "dgt-CVM_161274")
    assert (zaragoza.road, zaragoza.km_from, zaragoza.km_to) == ("Z-40", 26.6, 29.7)
    assert zaragoza.start == (41.6088, -0.915697) and zaragoza.end == (41.6192, -0.9496)
    assert zaragoza.direction == "MADRID"


@pytest.mark.parametrize("raw", ["3", "03", "3,46"])
def test_dgt_one_digit_provinces_are_selected_as_ine_codes(raw):
    # The DGT file writes Alicante as "3", not "03"
    xml = (FIX / "dgt_radares.xml").read_bytes()
    radars = dgt.parse(xml, provinces.parse(raw))
    assert [(r.id, r.province) for r in radars] == [("dgt-CABINACINEMOMETRO_120154", "03")]


# ---- a failed source keeps its zones


def fake_source(key, behaviour, provinces=None):
    def fetch(ctx):
        answer = behaviour()
        if isinstance(answer, Exception):
            raise answer
        return answer

    return Source(key, fetch, "test", "test", provinces=provinces)


def one_radar(n=1):
    return SourceResult(
        radars=[Radar(f"t-{n}", "t", "fixed", f"Radar {n}", 37.0, -1.0 + n / 100, 500)]
    )


def test_a_failed_source_reuses_its_last_good_result():
    answers = iter([one_radar(), OSError("504"), OSError("504")])
    source = fake_source("t", lambda: next(answers))
    first = sources.run(source, ctx(), now=1000.0)
    assert first.up and first.fetched_at == 1000.0
    failed = sources.run(source, ctx(), now=5000.0)
    assert not failed.up and failed.fetched_at == 1000.0 and "504" in failed.error
    assert failed.result.radars == one_radar().radars
    # another province: the stored result does not apply
    other = sources.run(source, ctx(provinces=frozenset({"28"})), now=6000.0)
    assert not other.up and other.fetched_at is None and other.result.radars == []


def test_a_source_that_never_succeeded_adds_nothing_and_does_not_raise():
    outcome = sources.run(fake_source("t", lambda: ValueError("parser broke")), ctx())
    assert outcome.result == SourceResult() and not outcome.up and outcome.fetched_at is None


def test_the_last_good_result_round_trips_every_field():
    radar = Radar(
        "a", "murcia", "mobile_announced", "Radar anunciado X", 37.1, -1.1, 478,
        valid_from=date(2026, 9, 28), valid_to=date(2026, 10, 4), url="u", attribution="at",
        maxspeed=40, direction="N", province="30", active=False,
    )  # fmt: skip
    stretch = Stretch(
        "s", "dgt", "Tramo", "A-7", (37.0, -1.0), (37.1, -1.1), ((37.0, -1.0), (37.1, -1.1)),
        1.5, 3.0, 100, "MADRID", "30", "u", "at",
    )  # fmt: skip
    week = WeeklyList("murcia", MONDAY, MONDAY, [cli.Announced("Calle", None)], [])
    result = SourceResult([radar], [stretch], [week], updated="2026-09-17")
    store.save_result("x", "fp", result, now=1.0)
    assert store.load_result("x", "fp") == (result, 1.0)
    assert store.load_result("x", "other settings") is None


def test_collect_keeps_a_failed_sources_radars_and_reports_it(monkeypatch):
    up = {"a": True}

    def behaviour():
        return one_radar(1) if up["a"] else OSError("timed out")

    registry = {"a": fake_source("a", behaviour), "b": fake_source("b", lambda: one_radar(2))}
    monkeypatch.setattr(sources, "REGISTRY", registry)
    first = cli.collect(MONDAY)
    assert [r.id for r in first.radars] == ["t-1", "t-2"]
    up["a"] = False
    second = cli.collect(MONDAY)
    assert [r.id for r in second.radars] == ["t-1", "t-2"]
    state = metrics.State(3600)
    state.collected(second.radars, second.lists, second.source_status())
    text = state.render(now=second.outcomes[0].fetched_at + 90)
    assert 'radares_source_up{source="a"} 0' in text
    assert 'radares_source_up{source="b"} 1' in text
    assert 'radares_source_data_age_seconds{source="a"} 90.0' in text


def test_a_failed_refresh_of_an_old_download_reports_the_source_down(monkeypatch):
    # The download cache must not hide a source that stopped answering.
    xml = (FIX / "dgt_radares.xml").read_bytes()
    monkeypatch.setattr(net, "get", lambda url, **kw: xml)
    first = sources.run(dgt.SOURCE, ctx(), now=1000.0)
    assert first.up and first.result.radars
    month_ago = time.time() - 30 * 86_400
    for f in net.cache_dir().iterdir():
        if f.is_file():
            os.utime(f, (month_ago, month_ago))

    def down(url, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(net, "get", down)
    later = sources.run(dgt.SOURCE, ctx(), now=1000.0 + 30 * 86_400)
    assert (later.up, later.fetched_at) == (False, 1000.0)
    assert later.result.radars == first.result.radars
    state = metrics.State(3600)
    state.collected(later.result.radars, [], {"dgt": (later.up, later.fetched_at)})
    text = state.render(now=1000.0 + 30 * 86_400)
    assert 'radares_source_up{source="dgt"} 0' in text
    assert f'radares_source_data_age_seconds{{source="dgt"}} {30 * 86_400.0}' in text


def test_a_reused_list_of_another_week_counts_as_missing_this_week():
    last_week = date(2026, 9, 21)
    skipped = cli.Announced("Calle Perdida", "El Raal")
    listed = WeeklyList("m", last_week, last_week, [cli.Announced("Calle", None)], [skipped])
    answers = iter([SourceResult(radars=announced(last_week), lists=[listed]), OSError("403")])
    source = fake_source("m", lambda: next(answers))
    sources.run(source, ctx(day=last_week), now=1000.0)
    failed = sources.run(source, ctx(day=MONDAY), now=2000.0)
    assert not failed.up
    assert failed.result.lists == [WeeklyList("m", MONDAY)]  # not found, nothing skipped
    state = metrics.State(3600)
    state.collected(failed.result.radars, failed.result.lists, {"m": (False, 1000.0)})
    text = state.render(now=datetime(2026, 9, 29, tzinfo=SPAIN).timestamp())  # Tuesday 00:00
    assert 'radares_weekly_list_found{source="m"} 0' in text
    assert 'radares_weekly_list_missing_seconds{source="m"} 86400.0' in text
    assert "radares_street_skipped{" not in text
    # the same week reused stays found
    answers = iter([SourceResult(lists=[WeeklyList("m", MONDAY, MONDAY)]), OSError("403")])
    source = fake_source("m", lambda: next(answers))
    sources.run(source, ctx(), now=1000.0)
    assert sources.run(source, ctx(), now=2000.0).result.lists == [WeeklyList("m", MONDAY, MONDAY)]


# ---- dormant streets


def announced(week: date, name="Radar anunciado Calle Mayor (El Raal)", n=2):
    end = date.fromordinal(week.toordinal() + 6)
    return [
        Radar(f"m-{week}-{i}", "m", "mobile_announced", name, 38.0 + i / 100, -1.0, 478,
              valid_from=week, valid_to=end, province="30")
        for i in range(n)
    ]  # fmt: skip


def test_a_street_stays_dormant_after_its_week_then_ages_out():
    week = announced(MONDAY)
    dormant, history = feed.remember([], week, MONDAY, 26)
    assert dormant == [] and history == week
    next_week = date(2026, 10, 5)
    dormant, history = feed.remember(history, [], next_week, 26)
    assert [(r.name, r.lat, r.lon, r.radius_m, r.active) for r in dormant] == [
        (r.name, r.lat, r.lon, r.radius_m, False) for r in week
    ]
    # 26 weeks after the period ended it is still there; a day later it is gone
    assert feed.remember(history, [], date(2027, 4, 4), 26)[0]
    assert feed.remember(history, [], date(2027, 4, 5), 26) == ([], [])
    assert feed.remember(history, [], next_week, 0) == ([], [])


def test_a_street_in_force_stays_active_when_its_source_gives_nothing():
    # The source failed and its last good result did not apply (other settings):
    # a street announced until Sunday must keep alerting until Sunday.
    _, history = feed.remember([], announced(MONDAY), MONDAY, 26)
    kept, _ = feed.remember(history, [], date(2026, 10, 1), 26)
    assert len(kept) == 2 and all(r.active for r in kept)
    kept, _ = feed.remember(history, [], date(2026, 10, 5), 26)
    assert len(kept) == 2 and not any(r.active for r in kept)


def test_a_street_announced_again_is_active_with_its_new_circles():
    _, history = feed.remember([], announced(MONDAY), MONDAY, 26)
    later = date(2026, 10, 12)
    again = announced(later, n=3)
    dormant, history = feed.remember(history, again, later, 26)
    assert dormant == [] and history == again


def test_merge_keeps_dormant_radars_and_lets_an_active_one_win_a_spot():
    active = [replace(r, lat=r.lat + 0.5) for r in announced(date(2026, 10, 5), name="Radar B")]
    dormant = [replace(r, active=False) for r in announced(MONDAY)]
    # an active radar whose id sorts last, at the spot of a dormant one
    same_spot = replace(active[0], id="zz", lat=dormant[0].lat)
    merged = feed.merge(active + dormant + [same_spot], date(2026, 10, 6))
    assert [r.active for r in merged] == [True, True, True, False]
    assert "zz" in {r.id for r in merged} and dormant[0].id not in {r.id for r in merged}


def test_collect_turns_last_weeks_street_dormant_and_back(monkeypatch):
    week = {"radars": announced(MONDAY)}
    src = fake_source("m", lambda: SourceResult(radars=week["radars"]), provinces=frozenset({"30"}))
    monkeypatch.setattr(sources, "REGISTRY", {"m": src})
    assert all(r.active for r in cli.collect(MONDAY).radars)
    week["radars"] = []  # the next Monday, no list yet
    found = cli.collect(date(2026, 10, 5)).radars
    assert len(found) == 2 and not any(r.active for r in found)
    assert json.loads(feed.to_geojson(found))["features"][0]["properties"]["active"] is False
    week["radars"] = announced(date(2026, 10, 5))
    found = cli.collect(date(2026, 10, 5)).radars
    assert len(found) == 2 and all(r.active for r in found)


def test_osm_notes_is_read_only_when_named(monkeypatch):
    """Notes give no zone, so an install gains nothing from them, and every install
    asking OSM's editing API each day is the load its usage policy warns about."""
    selected = lambda raw: [s.key for s in sources.selected(raw, None)]  # noqa: E731
    assert "osm_notes" not in selected(None)
    assert selected(["osm_notes"]) == ["osm_notes"]
    assert selected(["default", "osm_notes"]) == list(sources.REGISTRY)
    assert selected(["default"]) == selected(None)
    # through the environment, as a run reads it
    notes = replace(fake_source("osm_notes", lambda: SourceResult()), default=False)
    monkeypatch.setattr(
        sources, "REGISTRY", {"dgt": fake_source("dgt", lambda: SourceResult()), "osm_notes": notes}
    )
    monkeypatch.setenv("RADARES_PROVINCES", "all")
    monkeypatch.delenv("RADARES_SOURCES", raising=False)
    assert [o.key for o in cli.collect(MONDAY, save_history=False).outcomes] == ["dgt"]
    monkeypatch.setenv("RADARES_SOURCES", "default,osm_notes")
    assert [o.key for o in cli.collect(MONDAY, save_history=False).outcomes] == ["dgt", "osm_notes"]
    # the published feed is the one reader
    root = Path(__file__).parent.parent / ".github"
    jobs = yaml.safe_load((root / "workflows" / "feed.yml").read_text())["jobs"]
    for job in ("publish", "check"):  # both build through the shared action
        build = next(st for st in jobs[job]["steps"] if st.get("name") == "Build the feed")
        assert build["uses"] == "./.github/actions/build-feed", job
    action = yaml.safe_load((root / "actions" / "build-feed" / "action.yml").read_text())
    step = next(st for st in action["runs"]["steps"] if st.get("name") == "Build the feed")
    assert step["env"]["RADARES_SOURCES"] == "default,osm_notes"


def test_a_remembered_street_gives_no_zone_once_its_source_or_province_is_deselected(monkeypatch):
    src = fake_source(
        "m", lambda: SourceResult(radars=announced(MONDAY)), provinces=frozenset({"30"})
    )
    other = fake_source("o", lambda: SourceResult())
    monkeypatch.setattr(sources, "REGISTRY", {"m": src, "o": other})
    monkeypatch.setenv("RADARES_PROVINCES", "all")
    assert len(cli.collect(MONDAY).radars) == 2
    monkeypatch.setenv("RADARES_SOURCES", "o")
    assert cli.collect(MONDAY).radars == []
    monkeypatch.delenv("RADARES_SOURCES")
    monkeypatch.setenv("RADARES_PROVINCES", "28")
    assert cli.collect(MONDAY).radars == []
    # the history itself survives, so selecting it again brings the street back
    monkeypatch.setenv("RADARES_PROVINCES", "all")
    monkeypatch.setattr(
        sources, "REGISTRY", {"m": fake_source("m", lambda: SourceResult()), "o": other}
    )
    assert len(cli.collect(MONDAY).radars) == 2


@pytest.mark.parametrize(("weeks", "day"), [("0", date(2026, 10, 5)), ("1", date(2026, 10, 12))])
def test_only_a_real_sync_writes_the_history_of_announced_streets(monkeypatch, weeks, day):
    # Trying another RADARES_DORMANT_WEEKS with `feed` or `sync --dry-run` must
    # not cost the running service its dormant streets.
    src = fake_source("m", lambda: SourceResult(radars=announced(MONDAY)))
    monkeypatch.setattr(sources, "REGISTRY", {"m": src})
    cli.collect(MONDAY)
    before = store.load_announced()
    assert len(before) == 2
    monkeypatch.setenv("RADARES_DORMANT_WEEKS", weeks)
    monkeypatch.setattr(sources, "REGISTRY", {"m": fake_source("m", lambda: SourceResult())})
    assert cli.collect(day, save_history=False).radars == []
    assert store.load_announced() == before
    cli.collect(day)  # a real sync with that setting does forget them
    assert store.load_announced() == []


def test_the_feed_writes_stretches_as_lines_and_flags_every_feature():
    result = dgt.parse_all((FIX / "dgt_radares.xml").read_bytes(), {"30"})
    data = json.loads(feed.to_geojson(result.radars, result.stretches))
    lines = [f for f in data["features"] if f["geometry"]["type"] == "LineString"]
    assert len(lines) == 2 and all(len(f["geometry"]["coordinates"]) == 2 for f in lines)
    assert all(f["properties"]["kind"] == "stretch" for f in lines)
    assert all("active" in f["properties"] for f in data["features"])
    assert all(f["properties"]["province"] == "30" for f in data["features"])
    start = lines[0]["geometry"]["coordinates"][0]
    assert distance_m((start[1], start[0]), (37.6, -1.0)) < 100_000  # lon, lat order


def test_osm_cameras_carry_their_limit_and_direction():
    radars = osm.parse((FIX / "osm_es_mc.json").read_bytes())
    leta = next(r for r in radars if r.name == "Radar A-7 km 758.79")
    assert (leta.maxspeed, leta.direction) == (120, "200")


def test_murcia_registers_its_province():
    assert murcia.SOURCE.provinces == {"30"} and murcia.PROVINCE == "30"
