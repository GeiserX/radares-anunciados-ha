"""The ways a street the name search misses is still found, one test per kind of miss.

The geocoder answers are real (CartoCiudad and Nominatim, 23 Oct 2026), trimmed,
served by a fake ``Geocoder._get``: tests never touch the network.
"""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from radares_anunciados import geocode
from radares_anunciados.geo import distance_m
from radares_anunciados.geocode import Geocoder, choose, geometry_credit, wkt_lines
from radares_anunciados.model import Announced, WeeklyList
from radares_anunciados.sources import murcia
from radares_anunciados.streetnames import expand
from radares_anunciados.streets import (
    loose_key,
    name_regex,
    place,
    road_number,
    similarity,
    street_key,
)

FIX = Path(__file__).parent / "fixtures"
CARTO = json.loads((FIX / "cartociudad_murcia_2026-10-23.json").read_text("utf-8"))
NOMINATIM = json.loads((FIX / "nominatim_molino_batan_2026-10-23.json").read_text("utf-8"))
EL_ESPARRAGAL = (38.045, -1.11)
EL_RAAL = (38.04, -1.03)
LOS_DOLORES = (37.9769428, -1.105154)


@pytest.fixture
def answers(monkeypatch):
    """A fake ``Geocoder._get`` over the fixtures; returns the requests it served."""
    asked: list[tuple[str, dict]] = []

    def fake(self, provider, url, params):
        if self.budget <= 0:
            raise geocode._OutOfBudget
        self.budget -= 1
        asked.append((provider, dict(params)))
        if provider == "nominatim":
            return NOMINATIM if fold_q(params["street"]) == "carril molino batan" else []
        if url.endswith("/find"):
            return CARTO["find"].get(params["id"])
        if params.get("poblacion_filter") == "El Raal" and params["q"] == "Calle Mayor":
            return CARTO["candidates Calle Mayor El Raal"]
        return CARTO.get(f"candidates {params['q']}", [])

    monkeypatch.setattr(Geocoder, "_get", fake)
    return asked


def fold_q(text: str) -> str:
    from radares_anunciados.streets import fold

    return fold(text)


# ---- the name written another way -----------------------------------------


@pytest.mark.parametrize(
    "written, mapped",
    [
        ("Carril Molino Batán", "Carril Molino del Batán"),  # a small word dropped
        ("Camino Mota del Reguerón", "CAMINO MOTA REGUERON"),  # and added
        ("Calzada Aldapeta", "CALZADA ALDAPETA DE"),  # CartoCiudad's trailing particle
        ("Avenida Juan Carlos 1", "Avenida Juan Carlos I"),  # numbers as roman numerals
        ("Calle Miracruz", "Miracruz kalea"),  # Basque street type, written last
        ("Avenida Tolosa", "Tolosa etorbidea"),
        ("Calle Major", "Carrer Major"),  # Catalan and Valencian
        ("Plaza Nova", "Praza Nova"),  # Galician
    ],
)
def test_the_same_street_written_another_way(written, mapped):
    assert loose_key(written)[1] == loose_key(mapped)[1]
    assert similarity(written, mapped) == 1.0


def test_co_official_street_types_count_as_the_spanish_type():
    assert street_key("Miracruz kalea") == ("calle", "miracruz")
    assert street_key("Carrer de la Diputació") == ("calle", "diputacio")
    assert street_key("Rúa do Franco")[0] == "calle"
    # a street named after a type keeps its name
    assert street_key("Calle Plaza") == ("calle", "plaza")


def test_a_trailing_abbreviation_is_the_street_type():
    assert expand("San Ignacio de Loyola Avda.") == "Avenida San Ignacio de Loyola"
    assert expand("Padre Isla Avda") == "Avenida Padre Isla"


def test_a_typo_is_alike_but_another_street_is_not():
    assert similarity("Avenida San Ignacio de Loyoa", "Avenida San Ignacio de Loyola") > 0.9
    assert similarity("Calle Mayor", "Calle Menor") < geocode.SIMILAR
    assert similarity("Calle Mayor", "Calle Mayor de Abajo") < geocode.SIMILAR
    # a different number is a different street, however alike the letters
    assert similarity("Avenida Juan XXIII", "Avenida Juan XXII") == 0.0


def test_the_overpass_regex_takes_small_words_and_basque_types():
    regex = re.compile(name_regex("Carril Molino Batán"), re.I)
    assert regex.search("Carril Molino del Batán")
    assert regex.search("Carril Molino Batán")
    assert not regex.search("Carril Molino Batán Nuevo")
    basque = re.compile(name_regex("Calle Miracruz"), re.I)
    assert basque.search("Miracruz kalea") and basque.search("Calle Miracruz")
    roman = re.compile(name_regex("Avenida Juan Carlos I"), re.I)
    assert roman.search("Avenida Juan Carlos 1") and roman.search("Avenida Juan Carlos I")


def _way(id_, name, line, **tags):
    return {
        "type": "way",
        "id": id_,
        "tags": {"name": name, **tags} if name else tags,
        "geometry": [{"lat": a, "lon": b} for a, b in line],
    }


LA_RAYA = {"type": "node", "id": 1, "lat": 37.985, "lon": -1.183, "tags": {"name": "La Raya"}}
EL_PALMAR = {"type": "node", "id": 2, "lat": 37.94, "lon": -1.16, "tags": {"name": "El Palmar"}}


def test_place_finds_a_name_with_a_small_word_more():
    overpass = json.dumps(
        {
            "elements": [
                LA_RAYA,
                _way(10, "Carril Molino del Batán", [(37.983, -1.1846), (37.9842, -1.181)]),
            ]
        }
    ).encode()
    item = Announced("Carril Molino Batán", "La Raya")
    placed = place([item], overpass, 300)[item]
    assert placed.centres and placed.method == "osm_fuzzy"


def test_place_follows_the_road_a_road_number_names():
    overpass = json.dumps(
        {
            "elements": [
                EL_PALMAR,
                _way(11, None, [(37.94, -1.15), (37.945, -1.14)], ref="MU-30", highway="motorway"),
            ]
        }
    ).encode()
    item = Announced("Auxiliar MU-30", "El Palmar")
    assert road_number(item.street) == "MU-30"
    assert road_number("Carretera Auxiliar A-30") == "A-30"
    placed = place([item], overpass, 300)[item]
    assert placed.centres and placed.method == "osm_road"


def test_the_ways_query_asks_for_the_road_number_too():
    from radares_anunciados.streets import ways_query

    places = json.dumps({"elements": [EL_PALMAR]}).encode()
    q = ways_query([Announced("Auxiliar MU-30", "El Palmar")], places, murcia.BBOX)
    assert '["ref"~"(^|;) *MU-30( *;|$)"](around:5000,37.940000,-1.160000)' in q


def test_a_street_found_by_its_exact_name_says_so():
    overpass = json.dumps(
        {"elements": [LA_RAYA, _way(12, "Carril Molino Batán", [(37.983, -1.1846)])]}
    ).encode()
    item = Announced("Carril Molino Batán", "La Raya")
    assert place([item], overpass, 300)[item].method == "osm"


# ---- the geocoders ------------------------------------------------------------


def test_cartociudad_places_a_street_osm_has_no_name_for(answers):
    item = Announced("Calle Marbella", "El Esparragal")
    overpass = json.dumps(
        {
            "elements": [
                {
                    "type": "node",
                    "id": 3,
                    "lat": 38.045,
                    "lon": -1.11,
                    "tags": {"name": "El Esparragal"},
                }
            ]
        }
    ).encode()
    placed = place([item], overpass, 300, Geocoder(murcia.TOWN))[item]
    assert placed.method == "cartociudad"
    assert placed.centres
    assert all(distance_m(c, EL_ESPARRAGAL) < 1500 for c in placed.centres)
    # asked inside the municipality
    assert all(p.get("municipio_filter") in (None, "Murcia") for _, p in answers)


def test_a_geocoded_street_far_from_its_district_is_rejected(answers):
    found = Geocoder(murcia.TOWN).street("Calle Marbella", "Los Dolores", [LOS_DOLORES])
    assert found is None  # 8 km from Los Dolores: another street, or no street


def test_a_district_not_on_the_map_must_be_in_the_address(answers):
    g = Geocoder(murcia.TOWN)
    found = g.street("Calle Mayor", "El Raal", [])
    assert found and found.method == "cartociudad"
    assert len(found.lines) == 16  # both of CartoCiudad's records of the street
    assert all(distance_m(line[0], EL_RAAL) < 3000 for line in found.lines)
    assert g.street("Calle Mayor", "Nowhere", []) is None


def test_every_piece_of_a_street_near_its_district_is_kept(answers):
    found = Geocoder(murcia.TOWN).street("Calle Mayor", "El Raal", [EL_RAAL])
    assert found and len(found.lines) == 16  # two records, one street


def test_a_street_whose_id_names_another_municipality_is_never_taken(answers):
    # CartoCiudad lists "CALLE MAYOR, Alcantarilla" under Murcia, id 30005...
    assert Geocoder(murcia.TOWN).street("Calle Mayor", "Alcantarilla", []) is None


def test_namesakes_and_no_district_give_no_answer(answers):
    # Murcia has a Calle Mayor in Alcantarilla, Los Garres, La Murta...
    assert Geocoder(murcia.TOWN).street("Calle Mayor", None, []) is None


def test_nominatim_answers_when_cartociudad_has_nothing(answers):
    found = Geocoder(murcia.TOWN).street("Carril Molino Batán", "La Raya", [(37.985, -1.183)])
    assert found and found.method == "nominatim"
    assert found.name == "Carril Molino del Batán"
    providers = [p for p, _ in answers]
    assert providers.index("cartociudad") < providers.index("nominatim")


def test_choose_only_takes_a_name_close_enough():
    line = [[(38.0, -1.1), (38.001, -1.1)]]
    near = [(38.0, -1.1)]
    assert not choose(
        "Calle Mayor", None, near, [{"name": "CALLE MENOR", "where": "", "lines": line}]
    )
    assert choose("Calle Mayor", None, near, [{"name": "CALLE MAYOR", "where": "", "lines": line}])


def test_answers_are_cached_found_and_not_found(answers):
    g = Geocoder(murcia.TOWN)
    g.street("Calle Marbella", "El Esparragal", [EL_ESPARRAGAL])
    g.street("Calle Inventada", "El Palmar", [])
    asked = len(answers)
    again = Geocoder(murcia.TOWN)  # the next run reads the file
    assert again.street("Calle Marbella", "El Esparragal", [EL_ESPARRAGAL])
    assert again.street("Calle Inventada", "El Palmar", []) is None
    assert len(answers) == asked


def test_a_geocoder_that_is_down_is_asked_again_next_run(monkeypatch):
    calls = []

    def down(self, provider, url, params):
        calls.append(provider)
        raise OSError("HTTP Error 502")

    monkeypatch.setattr(Geocoder, "_get", down)
    assert Geocoder(murcia.TOWN).street("Calle Marbella", "El Esparragal", []) is None
    first = len(calls)
    assert Geocoder(murcia.TOWN).street("Calle Marbella", "El Esparragal", []) is None
    assert len(calls) == 2 * first  # nothing was cached


def test_a_run_stops_asking_when_its_budget_is_spent(answers):
    g = Geocoder(murcia.TOWN, budget=2)
    assert g.street("Calle Marbella", "El Esparragal", [EL_ESPARRAGAL]) is None
    assert len(answers) == 2


def test_wkt_lines():
    assert wkt_lines("LINESTRING(-1.1 38.0,-1.2 38.1)") == [[(38.0, -1.1), (38.1, -1.2)]]
    assert wkt_lines("MULTILINESTRING((-1 38,-1 39),(-2 37,-2 36))") == [
        [(38.0, -1.0), (39.0, -1.0)],
        [(37.0, -2.0), (36.0, -2.0)],
    ]
    assert wkt_lines(None) == []


def test_nominatim_is_asked_at_most_once_a_second(monkeypatch):
    sleeps = []
    monkeypatch.setattr(geocode.time, "sleep", sleeps.append)
    monkeypatch.setattr(geocode, "_last", {})
    geocode._wait("nominatim")  # never asked: no wait
    assert sleeps == []
    geocode._last["nominatim"] = geocode.time.monotonic()
    geocode._wait("nominatim")
    assert len(sleeps) == 1 and 1.0 < sleeps[0] <= 1.1


# ---- credit and report ----------------------------------------------------------


def test_a_radar_placed_from_cartociudad_credits_it():
    osm = murcia.ATTRIBUTION
    assert geometry_credit(osm, "osm") == osm
    assert geometry_credit(osm, "nominatim") == osm
    carto = geometry_credit(osm, "cartociudad")
    assert "OpenStreetMap" not in carto and "CartoCiudad, CC BY 4.0 scne.es" in carto
    council = "Ayuntamiento de Donostia / San Sebastián, ubicación del radar móvil"
    assert geometry_credit(council, "council") == council
    assert geometry_credit(council, "nominatim").endswith("geometría © OpenStreetMap")
    assert "CartoCiudad" in murcia.SOURCE.attribution and "CartoCiudad" in murcia.SOURCE.licence


def test_status_reports_how_each_street_was_placed():
    from radares_anunciados.cli import street_report

    w = WeeklyList(
        "murcia",
        date(2026, 10, 19),
        streets=[Announced("A", "x"), Announced("B", "y"), Announced("C", "z")],
        skipped=[Announced("C", "z")],
        placed={"A (x)": "osm", "B (y)": "cartociudad"},
    )
    assert street_report([w]) == {
        "announced": 3,
        "placed": 2,
        "by_method": {"cartociudad": 1, "osm": 1},
        "unplaced": ["C (z)"],
    }


def test_the_methods_survive_the_last_good_result():
    from radares_anunciados import store

    w = WeeklyList("leon", date(2026, 10, 19), placed={"A": "osm_fuzzy"})
    assert store.list_from_json(store.list_to_json(w)).placed == {"A": "osm_fuzzy"}
    old = store.list_to_json(w)
    del old["placed"]  # a file written before the field existed
    assert store.list_from_json(old).placed == {}


# ---- León and Donostia ----------------------------------------------------------


@pytest.fixture
def leon_map():
    from radares_anunciados.sources import leon

    return leon.StreetMap((FIX / "leon_overpass.json").read_bytes())


class FakeGeocoder:
    """Answers one street with a short line; records what it was asked."""

    def __init__(self, street, line, method="cartociudad"):
        self.answer = (street, line, method)
        self.asked = []

    def street(self, street, district, anchors):
        self.asked.append(street)
        name, line, method = self.answer
        return geocode.Found([line], method, name.upper()) if street == name else None


def test_leon_takes_a_typo_when_one_name_is_alike(leon_map):
    found = leon_map.find(leon_map.announced("Avda. Padre Isl"))
    assert found.ways and found.method == "osm_fuzzy"
    assert found.item.street == "Avenida del Padre Isla"


def test_leon_reads_a_trailing_avda(leon_map):
    found = leon_map.find(leon_map.announced("San Ignacio de Loyola Avda."))
    assert found.ways and found.item.street == "Avenida San Ignacio de Loyola"


def test_leon_asks_the_geocoder_for_a_street_not_on_its_map(leon_map):
    from radares_anunciados.sources import leon

    line = [(42.6, -5.57), (42.601, -5.571)]  # inside León
    geo = FakeGeocoder("Calle Recién Abierta", line)
    slots = [leon.Slot(date(2026, 10, 20), "Calle Recién Abierta", 30)]
    schedule = leon.Schedule("u", date(2026, 9, 28), slots, leon.COUNCIL_ATTRIBUTION)
    week = leon.week_of(date(2026, 10, 20))
    radars, status = leon.to_radars([schedule], leon_map, lambda limit: 150, week, geo)
    assert radars and not status.skipped
    assert set(status.placed.values()) == {"cartociudad"}
    assert {r.name for r in radars} == {"Radar anunciado Calle Recién Abierta"}
    assert all("CartoCiudad, CC BY 4.0" in r.attribution for r in radars)
    # without a geocoder it stays skipped, as before
    radars, status = leon.to_radars([schedule], leon_map, lambda limit: 150, week)
    assert not radars and status.skipped


def test_donostia_asks_the_geocoder_for_a_street_the_council_draws_no_line_for():
    from radares_anunciados.sources import donostia_movil as d

    script = (FIX / "donostia_javascript_geo_2019-06-22.js").read_bytes().decode(d.ENCODING)
    lines = d.parse_lines(script)
    geo = FakeGeocoder("Calle Miracruz", [(43.322, -1.975), (43.3222, -1.973)])
    methods: dict[str, str] = {}
    radars, skipped = d.to_radars(
        date(2021, 2, 16),
        ["Paseo Errondo", "Calle Miracruz", "Calle Nada"],
        lines,
        300,
        geo,
        methods,
    )
    assert skipped == ["Calle Nada"]
    assert methods == {
        "Paseo Errondo (Donostia)": "council",
        "Calle Miracruz (Donostia)": "cartociudad",
    }
    assert geo.asked == ["Calle Miracruz", "Calle Nada"]  # the council's line comes first
    miracruz = [r for r in radars if "Miracruz" in r.name]
    assert miracruz and all("CartoCiudad" in r.attribution for r in miracruz)
    assert all(r.attribution == d.ATTRIBUTION for r in radars if "Errondo" in r.name)


def test_the_published_run_keeps_the_geocoder_answers():
    from test_publish import kept_between_runs

    assert all("geocoded" in block for block in kept_between_runs())


def test_a_street_of_another_municipality_is_never_taken(monkeypatch):
    marbella = CARTO["candidates Calle Marbella"][0]
    neighbour = {**marbella, "muniCode": "30005", "muni": "Alcantarilla"}

    def fake(self, provider, url, params):
        if url.endswith("/find"):
            return CARTO["find"][marbella["id"]]
        return [neighbour] if provider == "cartociudad" else []

    monkeypatch.setattr(Geocoder, "_get", fake)
    assert Geocoder(murcia.TOWN).street("Calle Marbella", None, []) is None
