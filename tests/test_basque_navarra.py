"""Basque Government, Navarra and Donostia sources, on saved real answers."""

import re
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from radares_anunciados import feed, net, sources
from radares_anunciados.geo import densify, distance_m
from radares_anunciados.model import Announced
from radares_anunciados.sources import Context, dgt, donostia, donostia_movil, euskadi, navarra
from radares_anunciados.speed import Radius

FIX = Path(__file__).parent / "fixtures"
TRAFIKOA = FIX / "trafikoa_cabinas_2026-10-01.html"
NAVARRA_API = FIX / "navarra_radars_api_2026-10-01.json"
NAVARRA_VIEWER = FIX / "navarra_viewer_2026-10-01.html"
NAVARRA_JS = FIX / "navarra_main_trimmed_2026-10-01.js"
DGT_NAVARRA = FIX / "dgt_navarra_2026-10-01.xml"
DONOSTIA = FIX / "donostia_radarra_2026-10-01.json"
# Two Wayback captures of the mobile page with a plan, the live page on a day
# without one, and the only capture of the map script with lines (another day:
# the page and the script never were captured together with content).
PLAN_2025 = FIX / "donostia_radar_movil_2025-03-06.html"
PLAN_2021 = FIX / "donostia_radar_movil_2021-02-16.html"
NO_PLAN = FIX / "donostia_radar_movil_2026-10-01.html"
LINES = FIX / "donostia_javascript_geo_2019-06-22.js"


def ctx(day: date = date(2025, 3, 6), provinces=None) -> Context:
    return Context(day=day, provinces=provinces, boxes=(), radius=Radius())


def by_id(radars):
    return {r.id: r for r in radars}


# --- Basque Government (Trafikoa) -------------------------------------------


def test_euskadi_reads_every_booth_of_the_page():
    radars = by_id(euskadi.parse(TRAFIKOA.read_text(encoding="utf-8")))
    # 9 blocks in the fixture; the red-light camera of Getaria is no speed radar
    assert len(radars) == 8
    assert "euskadi-foto-rojo-getaria" not in radars
    max_center = radars["euskadi-max-center"]
    assert max_center.name == "Radar fijo A-8 km 123.5 (sentido DONOSTIA / SAN SEBASTIÁN)"
    assert max_center.kind == "fixed"
    assert max_center.province == "48"
    assert max_center.maxspeed == 80
    assert max_center.direction == "DONOSTIA / SAN SEBASTIÁN"
    assert max_center.url == euskadi.URL
    assert max_center.attribution == euskadi.ATTRIBUTION
    # x 499983.69, y 4792619.83 in UTM 30N: Barakaldo, on the A-8
    assert distance_m((max_center.lat, max_center.lon), (43.28640, -3.00020)) < 1
    assert radars["euskadi-trabakua-tramo-1"].kind == "section"
    assert radars["euskadi-trabakua-tramo-1"].name.startswith("Radar de tramo BI-633")
    assert {r.province for r in radars.values()} == {"01", "20", "48"}
    assert all(r.name.startswith("Radar") for r in radars.values())
    assert "euskadi-arminon" in radars  # ARMIÑON, ASCII id


@pytest.mark.parametrize(
    ("text", "kmh"),
    [("80 km/h", 80), ("80 Km/h", 80), ("80", 80), ("60/80 km/h", 80), ("-", None), ("", None)],
)
def test_euskadi_free_text_limits(text, kmh):
    assert euskadi.speed_kmh(text) == kmh


def test_euskadi_limits_on_the_page():
    radars = by_id(euskadi.parse(TRAFIKOA.read_text(encoding="utf-8")))
    assert radars["euskadi-kukularra"].maxspeed == 80  # "60/80 km/h"
    assert radars["euskadi-tunel-itziar"].maxspeed == 80  # "80"
    assert radars["euskadi-arminon"].maxspeed == 80  # "80 Km/h"


def test_euskadi_keeps_only_the_selected_provinces():
    radars = euskadi.parse(TRAFIKOA.read_text(encoding="utf-8"), frozenset({"01"}))
    assert sorted(r.id for r in radars) == ["euskadi-arminon", "euskadi-laguardia-vi"]


def test_euskadi_fails_on_a_page_without_radars(monkeypatch):
    monkeypatch.setattr(net, "cached_get", lambda url, **kw: b"<html>Mantenimiento</html>")
    with pytest.raises(ValueError, match="layout"):
        euskadi.fetch(ctx())


@pytest.mark.parametrize(
    "change",
    [
        # the territories renamed: every block in an unknown territory
        lambda page: (
            page.replace('"Araba"', '"Álava"')
            .replace('"Gipuzkoa"', '"Guipúzcoa"')
            .replace('"Bizkaia"', '"Vizcaya"')
        ),
        # the popup cut short: every block unreadable
        lambda page: re.sub(r"popupValores = \[.*?\]", "popupValores = []", page, flags=re.S),
    ],
)
def test_euskadi_fails_when_no_block_reads(monkeypatch, change):
    page = change(TRAFIKOA.read_text(encoding="utf-8"))
    assert euskadi._BLOCK.search(page)  # the blocks are there, none reads
    monkeypatch.setattr(net, "cached_get", lambda url, **kw: page.encode())
    with pytest.raises(ValueError, match="layout"):
        euskadi.fetch(ctx())


def test_euskadi_fetch_keeps_the_selected_provinces(monkeypatch):
    monkeypatch.setattr(net, "cached_get", lambda url, **kw: TRAFIKOA.read_bytes())
    radars = euskadi.fetch(ctx(provinces=frozenset({"01", "31"}))).radars
    assert sorted(r.id for r in radars) == ["euskadi-arminon", "euskadi-laguardia-vi"]


# --- Navarra -----------------------------------------------------------------


def test_navarra_api_lists_eight_radars_in_utm():
    radars = by_id(navarra.parse_api(NAVARRA_API.read_bytes()))
    assert len(radars) == 8
    alsasua = radars["navarra-A-1-401.5-C"]
    assert alsasua.name == "Radar fijo A-1 km 401.5 (sentido creciente)"
    assert alsasua.province == "31"
    assert alsasua.maxspeed is None  # not published
    # "N121APK25+9D": the part after + is the decimal part, km 25.9
    assert "navarra-N-121-A-25.9-D" in radars
    assert "navarra-A-12-5.9-D" in radars  # the one the bundle lacks


def test_navarra_bundle_and_api_agree():
    viewer = NAVARRA_VIEWER.read_text(encoding="utf-8")
    url = navarra.bundle_url(viewer)
    assert url.endswith("/gn.visortrafico.web.internet/assets/main-D0gC8mEA.js")
    bundle = by_id(navarra.parse_bundle(NAVARRA_JS.read_text(encoding="utf-8"), url))
    api = by_id(navarra.parse_api(NAVARRA_API.read_bytes()))
    assert len(bundle) == 7  # the list is written twice in the bundle
    assert set(bundle) == set(api) - {"navarra-A-12-5.9-D"}
    for rid, radar in bundle.items():
        # UTM 30N from the API against WGS84 from the bundle: Tudela matches to
        # the centimetre, the others differ by up to 30 m between the two lists.
        assert distance_m((radar.lat, radar.lon), (api[rid].lat, api[rid].lon)) < 50
        assert radar.name == api[rid].name
    assert (
        distance_m(
            (bundle["navarra-AP-68-218.2-C"].lat, bundle["navarra-AP-68-218.2-C"].lon),
            (api["navarra-AP-68-218.2-C"].lat, api["navarra-AP-68-218.2-C"].lon),
        )
        < 0.5
    )


def _bundle_get(url, **kw):
    if url == navarra.VIEWER:
        return NAVARRA_VIEWER.read_bytes()
    if url.endswith("main-D0gC8mEA.js"):
        return NAVARRA_JS.read_bytes()
    if url in dgt.URLS:
        raise OSError("DGT down")
    raise AssertionError(url)


def _with_dgt(url, **kw):
    return DGT_NAVARRA.read_bytes() if url in dgt.URLS else _bundle_get(url, **kw)


def _serve(monkeypatch, get):
    """``get`` answers both ``net.cached_get`` and the DGT file's dated download."""
    monkeypatch.setattr(net, "cached_get", get)
    monkeypatch.setattr(net, "cached_get_dated", lambda url, **kw: (get(url, **kw), None))


def test_navarra_reads_the_api_first(monkeypatch):
    monkeypatch.setattr(navarra, "post_api", lambda: NAVARRA_API.read_bytes())
    _serve(monkeypatch, _bundle_get)
    assert len(navarra.fetch(ctx()).radars) == 8  # DGT down: every radar is kept


def test_navarra_leaves_the_dgt_radars_to_the_dgt_source(monkeypatch):
    # The live DGT file lists 7 radars in Navarra, 4 to 508 m from the viewer's
    # and with the km cut differently: only A-12 km 5.9 is Navarra's alone.
    monkeypatch.setattr(navarra, "post_api", lambda: NAVARRA_API.read_bytes())
    _serve(monkeypatch, _with_dgt)
    radars = navarra.fetch(ctx()).radars
    assert [r.id for r in radars] == ["navarra-A-12-5.9-D"]
    official = dgt.parse(DGT_NAVARRA.read_bytes(), {"31"})
    assert len(official) == 7
    assert len(feed.merge(official + radars, ctx().day)) == 8


def test_navarra_same_radar_means_same_road_and_km():
    api = navarra.parse_api(NAVARRA_API.read_bytes())
    official = dgt.parse(DGT_NAVARRA.read_bytes(), {"31"})
    # N-121-A km 60.2 is 508 m from DGT's, the same booth by road and km
    n121 = [r for r in api if r.id == "navarra-N-121-A-60.2-C"]
    assert navarra.without_dgt(n121, official) == []
    # a km off by more than half a km, or another road, is another radar
    moved = [replace(n121[0], name="Radar fijo N-121-A km 61.2 (sentido creciente)")]
    assert navarra.without_dgt(moved, official) == moved
    other = [replace(n121[0], name="Radar fijo N-121-B km 60.2 (sentido creciente)")]
    assert navarra.without_dgt(other, official) == other


@pytest.mark.parametrize(
    "answer", [OSError("timed out"), b'{"status":200,"data":{"elemento":[]}}', b"<html>"]
)
def test_navarra_falls_back_on_the_bundle(monkeypatch, answer):
    def post():
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(navarra, "post_api", post)
    _serve(monkeypatch, _bundle_get)
    radars = navarra.fetch(ctx()).radars
    assert len(radars) == 7
    assert all(r.url.endswith("main-D0gC8mEA.js") for r in radars)
    # the bundle lacks A-12, so with the DGT file nothing is Navarra's alone
    _serve(monkeypatch, _with_dgt)
    assert navarra.fetch(ctx()).radars == []


def test_navarra_fails_when_both_fail(monkeypatch):
    def down(*a, **kw):
        raise OSError("no route")

    monkeypatch.setattr(navarra, "post_api", down)
    monkeypatch.setattr(net, "cached_get", down)
    with pytest.raises(OSError):
        navarra.fetch(ctx())


# --- Donostia fixed ------------------------------------------------------------


def test_donostia_fixed_radars():
    radars = donostia.parse(DONOSTIA.read_bytes())
    assert len(radars) == 12
    assert len({r.id for r in radars}) == 12
    first = radars[0]
    assert first.name == "Radar fijo Nafarroa hiribidea, Donostia"
    assert first.maxspeed == 30
    assert (round(first.lat, 5), round(first.lon, 5)) == (43.32215, -1.96616)
    assert {r.province for r in radars} == {"20"}
    assert {r.maxspeed for r in radars} == {30, 50}


def test_donostia_fixed_fails_on_an_empty_layer(monkeypatch):
    monkeypatch.setattr(net, "cached_get", lambda url, **kw: b'{"features": []}')
    with pytest.raises(ValueError):
        donostia.fetch(ctx())


# --- Donostia mobile, daily ---------------------------------------------------


def page(path: Path) -> str:
    return path.read_bytes().decode(donostia_movil.ENCODING)


def test_mobile_page_prints_month_first():
    day, streets = donostia_movil.parse_page(page(PLAN_2025))
    assert day == date(2025, 3, 6)  # "Durante el 03/06/2025", captured on 6 March
    assert streets == [
        "Avenida Alcalde José Elósegi",
        "Calle Peruene",
        "Paseo Berio",
        "Paseo del Urumea",
        "Paseo Errondo",
        "Paseo Izostegi",
    ]
    assert donostia_movil.parse_page(page(PLAN_2021))[0] == date(2021, 2, 16)


def test_mobile_page_without_a_plan():
    assert donostia_movil.parse_page(page(NO_PLAN)) == (None, [])


def test_mobile_page_with_another_layout_fails():
    with pytest.raises(ValueError):
        donostia_movil.parse_page("<h1>Ubicación del radar móvil</h1><p>Mantenimiento</p>")


def test_mobile_script_is_read_as_javascript():
    lines = donostia_movil.parse_lines(page(LINES))
    assert len(lines) == 24
    # single-quoted titles, accents in windows-1252
    assert "Avenida Alcalde José Elósegi" in lines
    lat, lon = lines["Avd Buenavista"][0][0]
    assert (round(lat, 6), round(lon, 6)) == (43.320067, -1.937291)  # 3D point, lon first


def test_js_object_quotes_and_trailing_commas():
    script = 'var a = 1; var puntos = {"k": \'it\\\'s "x"\', "l": [1, 2,],};\nvar b = {};'
    assert donostia_movil.js_object(script, "puntos") == {"k": 'it\'s "x"', "l": [1, 2]}


def test_js_object_skips_comments():
    script = """var puntos = {"features": [ // don't
        {"a": 'b' /* it's "here" */, "url": "http://x/y"}, // last
    ]};"""
    assert donostia_movil.js_object(script, "puntos") == {
        "features": [{"a": "b", "url": "http://x/y"}]
    }


@pytest.mark.parametrize(
    "script", ['var puntos = {"a": "x', "var puntos = {'a': 1 /* open", "var puntos = {'a\\"]
)
def test_js_object_unterminated_raises_value_error(script):
    with pytest.raises(ValueError):
        donostia_movil.js_object(script, "puntos")


def test_mobile_streets_are_covered_and_missing_ones_skipped():
    lines = donostia_movil.parse_lines(page(LINES))
    day, streets = donostia_movil.parse_page(page(PLAN_2021))
    radars, skipped = donostia_movil.to_radars(day, streets, lines, 478)
    assert skipped == ["Calle Miracruz", "Calzada Aldapeta"]  # no line in the script
    placed = {r.name for r in radars}
    assert len(placed) == len(streets) - 2
    for street in set(streets) - set(skipped):
        centres = [
            (r.lat, r.lon) for r in radars if r.name == f"Radar anunciado {street} (Donostia)"
        ]
        for line in lines[street]:
            for point in densify(line, 20):
                assert min(distance_m(point, c) for c in centres) <= 478
    # a line the page does not name stays out
    assert not any("Buenavista" in r.name for r in radars)


def _mobile_get(pages):
    calls = []

    def get(url, **kw):
        calls.append(url)
        return pages[url]

    return get, calls


def test_mobile_plan_is_valid_on_its_day_then_dormant(monkeypatch):
    get, _ = _mobile_get(
        {donostia_movil.PAGE: PLAN_2025.read_bytes(), donostia_movil.SCRIPT: LINES.read_bytes()}
    )
    monkeypatch.setattr(net, "get", get)
    day = date(2025, 3, 6)
    result = donostia_movil.fetch(ctx(day))
    assert result.radars
    for r in result.radars:
        assert r.kind == "mobile_announced"
        assert r.valid_from == r.valid_to == day
        assert r.province == "20"
        assert r.radius_m == Radius().street_m(None)
        assert r.url == donostia_movil.PAGE
    (status,) = result.lists
    assert status.published == day
    assert status.week == date(2025, 3, 3)
    assert Announced("Paseo Berio", "Donostia") in status.streets
    assert status.skipped == []
    # in force today
    assert feed.merge(result.radars, day)
    # the next day nothing is announced: the streets come back dormant
    remembered, _ = feed.remember(result.radars, [], day + timedelta(days=1), weeks=26)
    assert remembered and not any(r.active for r in remembered)
    assert {r.name for r in remembered} == {r.name for r in result.radars}


def test_mobile_day_without_plan_reads_no_script(monkeypatch):
    get, calls = _mobile_get({donostia_movil.PAGE: NO_PLAN.read_bytes()})
    monkeypatch.setattr(net, "get", get)
    today = date(2026, 10, 1)
    result = donostia_movil.fetch(ctx(today))
    assert result.radars == []
    (status,) = result.lists
    assert status.published == today and status.streets == []
    assert calls == [donostia_movil.PAGE]


def test_mobile_source_down_raises(monkeypatch):
    def down(url, **kw):
        raise OSError("timed out")

    monkeypatch.setattr(net, "get", down)
    with pytest.raises(OSError):
        donostia_movil.fetch(ctx())


# --- registry ------------------------------------------------------------------


def test_registry_terms_of_the_new_sources():
    reg = sources.REGISTRY
    assert reg["euskadi"].spanish_ip and reg["navarra"].spanish_ip
    assert not reg["donostia"].spanish_ip and not reg["donostia_movil"].spanish_ip
    assert reg["euskadi"].provinces == {"01", "20", "48"}
    assert reg["navarra"].provinces == {"31"}
    assert reg["donostia"].provinces == reg["donostia_movil"].provinces == {"20"}
    for key in ("euskadi", "navarra", "donostia", "donostia_movil"):
        assert reg[key].attribution and reg[key].licence
    # Donostia publishes its own reuse terms; the Basque and Navarra legal notices
    # reserve the site's content, and the feed says so next to its legal basis
    for key in ("donostia", "donostia_movil"):
        assert "https://www.donostia.eus/es/aviso-legal" in reg[key].licence
        assert "no reuse licence" not in reg[key].licence
    for key, url in (("euskadi", euskadi.LEGAL_URL), ("navarra", navarra.LEGAL_URL)):
        assert "Ley 37/2007" in reg[key].licence and "reserves" in reg[key].licence
        assert url in reg[key].licence
    # RADARES_SPANISH_IP_TIMEOUT caps the requests to these hosts, so they must
    # cover every URL the source reads that answers only from Spain
    for url, key in [
        (euskadi.URL, "euskadi"),
        (navarra.VIEWER, "navarra"),
        (navarra.API, "navarra"),
    ]:
        with net.fail_fast(20, reg[key].spanish_ip_hosts):
            assert net.timeout_s(90, url) == 20, url
        with net.fail_fast(20, reg[key].spanish_ip_hosts):
            assert net.timeout_s(90, dgt.URL) == 90  # the DGT file answers anywhere


def test_the_navarra_api_call_obeys_fail_fast(monkeypatch):
    asked = []

    def urlopen(request, timeout):
        asked.append(timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr(navarra.urllib.request, "urlopen", urlopen)
    with net.fail_fast(20, navarra.SOURCE.spanish_ip_hosts), pytest.raises(OSError):
        navarra.post_api()
    with pytest.raises(OSError):
        navarra.post_api()
    assert asked == [20, 60]
    # skipped outside their provinces
    selected = sources.selected(None, frozenset({"31"}))
    assert [s.key for s in selected] == ["dgt", "osm", "dgt_invive", "navarra"]
