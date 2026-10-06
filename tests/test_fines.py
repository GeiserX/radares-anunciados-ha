"""Barcelona and Madrid traffic fines: where mobile radars stood, by when a place fined."""

import json
import os
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from radares_anunciados import feed, ha, speed
from radares_anunciados.model import Radar
from radares_anunciados.sources import Context, barcelona_multas, madrid_multas
from radares_anunciados.sources.barcelona_multas import Day
from radares_anunciados.speed import Radius

FIX = Path(__file__).parent / "fixtures"
DAY = date(2026, 10, 2)
CTX = Context(day=DAY, provinces=None, boxes=(), radius=Radius())
Q4 = barcelona_multas.Quarter("5693bb33-9212-44df-9c67-a90a5fc06838", 2025, 4)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("RADARES_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RADARES_CACHE", str(tmp_path))


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


# ---- Barcelona -----------------------------------------------------------------

DAYS = "barcelona_multas_2025_4t_days.json"


@pytest.fixture(scope="module")
def bcn() -> dict[str, barcelona_multas.Place]:
    return {p.label: p for p in barcelona_multas.places(fixture(DAYS))}


def test_barcelona_reads_the_newest_quarter_of_the_datastore():
    quarter, updated = barcelona_multas.newest_quarter(
        fixture("barcelona_multas_package_2026-10-02.json")
    )
    assert quarter == Q4
    assert (quarter.first, quarter.last, quarter.days) == (
        date(2025, 10, 1),
        date(2025, 12, 31),
        92,
    )
    assert quarter.label() == "octubre a diciembre de 2025"
    assert updated == "2026-09-01"
    q1 = replace(Q4, quarter=1)
    assert (q1.first, q1.last, q1.days) == (date(2025, 1, 1), date(2025, 3, 31), 90)


def test_barcelona_asks_for_camera_speed_fines_by_place_and_day():
    sql = barcelona_multas.query(Q4.resource)
    assert "\"MITJA_IMPOSICIO\" = 'MTO'" in sql
    assert "'1210'" in sql and "'1232'" in sql and "'1225'" not in sql
    assert '"Data_Infraccio" d' in sql and "GROUP BY 1, 2, 3, 4, 5" in sql
    assert "min(NULLIF(trim(\"Hora_Infraccio\"), '')) h0" in sql
    assert barcelona_multas._hours("234400") == pytest.approx(23 + 44 / 60)
    assert barcelona_multas._hours("      ") is None


def test_barcelona_reads_each_place_with_its_days(bcn):
    # 53 places in the answer; one has no position
    assert len(bcn) == 52
    augusta = bcn["Via Augusta 331"]
    assert (augusta.lat, augusta.lon) == (41.4003214, 2.1228873)
    assert len(augusta.days) == 79 and augusta.days[0].day == date(2025, 10, 14)
    # a point outside the province of Barcelona is no position
    moved = fixture(DAYS).replace(b'"lat":"41.4003214"', b'"lat":"40.4003214"')
    assert "Via Augusta 331" not in {p.label for p in barcelona_multas.places(moved)}


@pytest.mark.parametrize(
    ("place", "why"),
    [
        ("Via Augusta 331", "fines over 12 h or more on 78 of 79 days"),  # a fixed camera
        ("Ronda Litoral (Llobregat) 96", "fines over 12 h or more on 8 of 10 days"),  # 1-10 Oct
        ("Ronda del Mig (Ascendent) 17", "fines over 12 h or more on 21 of 42 days"),
        ("Avinguda Diagonal 579", "fines on 28 days in a row"),  # every day 1-28 Oct
        ("Gran Via de les Corts Catalanes 329", "fines on 15 days in a row"),  # 1 or 2 a day
        ("Carrer de Muntaner 158", "fines on 75 of 92 days"),
        ("Carretera B-10 (Besòs) 176", ""),  # a week of short sessions: 7 days in a row
        ("Passeig de Garcia Fària 103", ""),
        ("Ronda de Dalt (Besòs) 160", ""),
    ],
)
def test_barcelona_tells_a_camera_from_a_mobile_radar_by_its_days_and_hours(bcn, place, why):
    assert barcelona_multas.camera_like(list(bcn[place].days), 92) == why


def test_barcelona_counts_a_session_past_midnight_once(bcn):
    # one session from 23:44 on 15 Oct to 01:59 on 16 Oct
    vall = barcelona_multas.sessions(list(bcn["Passeig de la Vall d'Hebron 178"].days))
    assert vall == [(date(2025, 10, 15), 7)]
    late = [Day(date(2025, 1, 1), 3, 22.0, 23.5), Day(date(2025, 1, 2), 2, 0.5, 1.0)]
    assert barcelona_multas.sessions(late) == [(date(2025, 1, 1), 5)]
    early = [Day(date(2025, 1, 1), 3, 18.0, 20.5), Day(date(2025, 1, 2), 2, 0.5, 1.0)]
    assert len(barcelona_multas.sessions(early)) == 2
    morning = [Day(date(2025, 1, 1), 3, 22.0, 23.5), Day(date(2025, 1, 2), 2, 9.0, 10.0)]
    assert len(barcelona_multas.sessions(morning)) == 2


def test_barcelona_gives_a_zone_only_where_a_mobile_radar_fined_on_two_days():
    result = barcelona_multas.parse(fixture(DAYS), Q4, "2026-09-01")
    names = sorted(r.name for r in result.radars)
    assert names == [
        "Radar móvil frecuente Avinguda d'Esplugues 24",
        "Radar móvil frecuente Avinguda del Parc Logístic 28",
        "Radar móvil frecuente Carrer A Zona Franca 104",
        "Radar móvil frecuente Carrer Número 6 Zona Franca 81",
        "Radar móvil frecuente Carrer de Mallorca 351",
        "Radar móvil frecuente Carrer de la Mare de Déu de Port 56",
        "Radar móvil frecuente Carretera B-10 (Besòs) 176",
        "Radar móvil frecuente Carretera B-10 (Llobregat) 160",
        "Radar móvil frecuente Passeig de Garcia Fària 103",
        "Radar móvil frecuente Passeig de la Bonanova 104",
        "Radar móvil frecuente Passeig de la Vall d'Hebron 282",
        "Radar móvil frecuente Ronda de Dalt (Besòs) 160",
        "Radar móvil frecuente Travessera de Gràcia 47",
    ]
    # Carrer A Zona Franca 93 fined once on each of two days; Vall d'Hebron 178 in
    # one session past midnight: no zone
    assert {r.kind for r in result.radars} == {"mobile_recurring"}
    b10 = next(r for r in result.radars if r.name.endswith("B-10 (Besòs) 176"))
    assert b10.id == "barcelona_multas-carretera-b-10-besos-00176"
    assert b10.attribution == (
        "Fuente de los datos: Ayuntamiento de Barcelona (CC BY 4.0), multas de tráfico de "
        "octubre a diciembre de 2025, actualizado 2026-09-01; multas aquí en 14 días de 92"
    )
    assert b10.maxspeed is None and b10.province == "08" and b10.valid_to is None
    assert result.updated == "2026-09-01"  # status.json shows it


def test_barcelona_names_a_place_the_same_in_every_quarter():
    q4 = barcelona_multas.parse(fixture(DAYS), Q4, "2026-09-01").radars
    # the next quarter: B-10 (Besòs) 176 fined on fewer days
    data = json.loads(fixture(DAYS))
    records = data["result"]["records"]
    b10 = [r for r in records if r["street"] == "Carretera B-10 (Besòs)" and r["num"] == "00176"]
    data["result"]["records"] = [r for r in records if r not in b10[:5]]
    later = barcelona_multas.parse(
        json.dumps(data).encode(), replace(Q4, year=2026, quarter=1)
    ).radars
    assert [(r.id, r.name, r.lat, r.lon) for r in q4] == [
        (r.id, r.name, r.lat, r.lon) for r in later
    ]
    # the count and the period live in the attribution
    before = next(r for r in q4 if r.name.endswith("176")).attribution
    after = next(r for r in later if r.name.endswith("176")).attribution
    assert before.endswith("14 días de 92") and after.endswith("10 días de 90")


def test_barcelona_merges_places_of_one_street_close_together():
    def place(street, num, lat):
        return barcelona_multas.Place(street, num, lat, 2.1, ())

    a = place("Carrer X", "0010", 41.4)
    b = place("Carrer X", "0020", 41.4009)  # 100 m
    c = place("Carrer X", "0090", 41.4036)  # 300 m from b
    d = place("Carrer Y", "0001", 41.4001)  # another street, 11 m
    merged = barcelona_multas._merge(
        [(b, {date(2025, 1, 2)}), (a, {date(2025, 1, 1)}), (c, {date(2025, 1, 3)}), (d, set())]
    )
    assert [(p.label, sorted(days)) for p, days in merged] == [
        ("Carrer X 10", [date(2025, 1, 1), date(2025, 1, 2)]),
        ("Carrer X 90", [date(2025, 1, 3)]),
        ("Carrer Y 1", []),
    ]


def test_barcelona_refuses_an_answer_without_places():
    with pytest.raises(ValueError, match="SQL failed"):
        barcelona_multas.parse(b'{"success": false, "error": {"message": "x"}}', Q4)
    empty = json.dumps({"success": True, "result": {"records": []}}).encode()
    with pytest.raises(ValueError, match="no camera place"):
        barcelona_multas.parse(empty, Q4)


def test_barcelona_fetch_queries_the_newest_quarter(monkeypatch):
    answers = {
        barcelona_multas.API: fixture("barcelona_multas_package_2026-10-02.json"),
        barcelona_multas.sql_url(Q4.resource): fixture(DAYS),
    }
    monkeypatch.setattr(barcelona_multas.net, "cached_get", lambda url, **kw: answers[url])
    assert len(barcelona_multas.SOURCE.fetch(CTX).radars) == 13
    assert barcelona_multas.SOURCE.provinces == {"08"}


# ---- Madrid --------------------------------------------------------------------

PACKAGE = "madrid_multas_package_2026-10-02.json"
REGISTER = "madrid_callejero_direcciones.csv"


def month_files() -> dict[str, str]:
    months = madrid_multas.months(fixture(PACKAGE))
    return {m.url: f"madrid_multas_{m.year}-{m.month:02d}.csv" for m in months[-3:]}


def lines(name: str) -> list[str]:
    return fixture(name).decode("latin-1").splitlines()


def test_madrid_finds_the_monthly_files_through_ckan():
    months = madrid_multas.months(fixture(PACKAGE))
    # oldest first; the TXT copy of a month, the grouped files and the PDF are not months
    assert [(m.year, m.month) for m in months] == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]
    assert months[-1].url.endswith("/202602detalle.csv")
    assert months[-1].label() == "febrero de 2026"
    assert months[-1].modified.startswith("2026-10-02")


def test_madrid_counts_the_speed_fines_of_places_coded_with_a_number():
    places = madrid_multas.parse_month(lines("madrid_multas_2026-02.csv"))
    # F040 AV PUERTA DE HIERRO is lamp post 40, not a street number: left out
    assert sorted(places) == [
        "N001 AV JUAN DE HERRERA",
        "N001 PO MORET",
        "N002 AV JUAN DE HERRERA",
        "N051 AV VALLADOLID",
        "N051 MARCENADO",
        "N094 ALCALDE SAINZ BARAND",
        "N128 JOSEFA VALCARCEL",
        "N320 EMBAJADORES",
        "N378 AV ARAGON",
    ]
    assert places["N378 AV ARAGON"] == {"fines": 3, "limits": {"60": 3}, "hours": [15]}
    header = lines("madrid_multas_2026-02.csv")
    with pytest.raises(ValueError, match="header"):
        madrid_multas.parse_month([header[0].replace("HORA", "H")] + header[1:])
    with pytest.raises(ValueError, match="no speed fine"):
        madrid_multas.parse_month([line for line in header if "VELOCIDAD" not in line])


@pytest.mark.parametrize(
    ("written", "kind", "name", "same"),
    [
        ("AV JUAN DE HERRERA", "avenida", "juan de herrera", True),
        ("AV JUAN DE HERRERA", "calle", "juan de herrera", False),  # the other street
        ("EMBAJADORES", "calle", "embajadores", True),  # no type: a Calle
        ("EMBAJADORES", "glorieta", "embajadores", False),
        ("ALCALDE SAINZ BARAND", "calle", "alcalde sainz de baranda", True),  # cut at 20
        ("PO GENERAL MARTINEZ", "paseo", "general martinez campos", True),  # cut after a word
        ("AV FCO J SAENZ OIZA", "avenida", "francisco javier saenz de oiza", True),
        ("ALFONSO XII", "calle", "alfonso xii", True),
        ("ALFONSO XII", "calle", "alfonso x", False),  # a numeral is no initial
        ("PO MORETO", "paseo", "moret", False),
        ("JOSEFA VALCARCEL", "calle", "josefa", False),
    ],
)
def test_madrid_matches_the_written_street_to_the_register(written, kind, name, same):
    assert madrid_multas.same_street(written, kind, name) is same


def test_madrid_labels_a_street_as_the_register_spells_it():
    label = madrid_multas._label
    assert label("CALLE", "DE", "JOSEFA VALCÁRCEL") == "Calle de Josefa Valcárcel"
    assert label("CALLE", "DE", "ALFONSO XII") == "Calle de Alfonso XII"
    assert label("CALLE", "DE", "O'DONNELL") == "Calle de O'Donnell"
    assert label("CALLE", "DEL", "ALCALDE SAINZ DE BARANDA") == (
        "Calle del Alcalde Sainz de Baranda"
    )
    assert label("PASEO", "", "IMPERIAL") == "Paseo Imperial"


@pytest.fixture(scope="module")
def register():
    numbers = {1, 2, 4, 6, 9, 33, 51, 52, 94, 128, 245, 320, 378}
    return madrid_multas.read_register(lines(REGISTER), numbers)


def test_madrid_places_a_place_at_its_number_in_the_register(register):
    streets, points = register
    street, (lat, lon) = madrid_multas.locate("N128 JOSEFA VALCARCEL", streets, points)
    assert street.label == "Calle de Josefa Valcárcel"
    assert (lat, lon) == pytest.approx((40.4494, -3.61408), abs=1e-4)
    # 245 is only in the register as 245 A, B and C, a few metres apart
    assert not isinstance(madrid_multas.locate("N245 ARTURO SORIA", streets, points), str)
    locate = madrid_multas.locate
    assert locate("N009 AV JUAN DE HERRERA", streets, points) == "no number 9 in the register"
    assert locate("N48M AV JUAN DE HERRERA", streets, points) == "48M is no house number"
    assert locate("N000 AV JUAN DE HERRERA", streets, points) == "0 is no house number"
    assert locate("N010 CALLE INVENTADA", streets, points) == "street not in the register"
    twin = madrid_multas.Street("calle", "josefa valcarcel", "Calle Josefa Valcárcel")
    assert locate("N128 JOSEFA VALCARCEL", [*streets, twin], points) == "several streets match"
    far = dict(points)
    key = (street.kind, street.name, 128)
    far[key] = [*points[key], ("", "PARCELA", (lat + 0.001, lon))]  # 111 m away
    assert locate("N128 JOSEFA VALCARCEL", streets, far) == (
        "number 128 is several places in the register"
    )


def test_madrid_finds_the_register_through_ckan():
    url, modified = madrid_multas.register_csv(fixture("madrid_callejero_package_2026-10-02.json"))
    assert url.endswith("/direccionesvigentes_20260927.csv")
    assert modified.startswith("2026-09-28")


def test_madrid_keeps_places_that_recur_in_mobile_radar_hours():
    one = {"A": {"fines": 3, "limits": {"50": 3}, "hours": [10, 11]}}
    two = {"A": {"fines": 2, "limits": {"40": 2}, "hours": [9]}, "B": one["A"]}
    assert madrid_multas.recurring([one, two]) == {"A": (2, Counter({"50": 3, "40": 2}))}
    all_day = {"A": {"fines": 30, "limits": {"50": 30}, "hours": list(range(18))}}
    assert madrid_multas.recurring([one, all_day]) == {}
    most = {"A": {"fines": 30, "limits": {"50": 30}, "hours": list(range(17))}}
    assert set(madrid_multas.recurring([one, most])) == {"A"}


def _fetch_fakes(monkeypatch, reads, downloads, asked=None):
    asked = [] if asked is None else asked
    answers = {
        madrid_multas.API: fixture(PACKAGE),
        madrid_multas.REGISTER_API: fixture("madrid_callejero_package_2026-10-02.json"),
    }

    def cached_get(url, **kw):
        asked.append(url)
        return answers[url]

    monkeypatch.setattr(madrid_multas.net, "cached_get", cached_get)
    files = month_files()

    def download(url):
        downloads.append(url)
        return madrid_multas.parse_month(lines(files[url]))

    def read_register(url, numbers):
        reads.append(sorted(numbers))
        return madrid_multas.read_register(lines(REGISTER), numbers)

    monkeypatch.setattr(madrid_multas, "_download", download)
    monkeypatch.setattr(madrid_multas, "_read_register", read_register)
    monkeypatch.setattr(madrid_multas, "MONTHS", 3)


def test_madrid_fetch_places_the_places_that_recur(monkeypatch, caplog):
    reads, downloads, asked = [], [], []
    _fetch_fakes(monkeypatch, reads, downloads, asked)
    with caplog.at_level("INFO"):
        result = madrid_multas.SOURCE.fetch(CTX)
    by_name = {r.name: r for r in result.radars}
    # December to February. Juan de Herrera 1 (December, February) takes in 2,
    # 30 m away; 4 fined in January only, Aragón 378 in February only.
    assert sorted(by_name) == [
        "Radar móvil frecuente Avenida de Juan de Herrera 1",
        "Radar móvil frecuente Avenida de Valladolid 51",
        "Radar móvil frecuente Calle de Embajadores 320",
        "Radar móvil frecuente Calle de Josefa Valcárcel 128",
        "Radar móvil frecuente Calle de Marcenado 51",
        "Radar móvil frecuente Calle del Alcalde Sainz de Baranda 94",
        "Radar móvil frecuente Paseo de Moret 1",
    ]
    assert "N001 AV JUAN DE HERRERA takes in ['N002 AV JUAN DE HERRERA']" in caplog.text
    herrera = by_name["Radar móvil frecuente Avenida de Juan de Herrera 1"]
    assert herrera.kind == "mobile_recurring" and herrera.province == "28"
    assert herrera.id == "madrid_multas-n001-av-juan-de-herrera"
    assert herrera.attribution == (
        "Origen de los datos: Ayuntamiento de Madrid (CC BY 4.0): multas de circulación de "
        "diciembre de 2025 a febrero de 2026, actualizado 2026-10-02, y callejero oficial; "
        "multas aquí en 3 de 3 meses"
    )
    moret = by_name["Radar móvil frecuente Paseo de Moret 1"]
    assert moret.maxspeed == 50 and moret.valid_to is None
    assert len(downloads) == 3 and len(reads) == 1
    assert result.updated == "2026-10-02"  # the newest month's file

    # The next run downloads nothing and reads no register: months and places are kept.
    reads.clear()
    downloads.clear()
    asked.clear()
    again = madrid_multas.SOURCE.fetch(CTX).radars
    assert [(r.id, r.name, r.lat, r.lon, r.maxspeed) for r in again] == [
        (r.id, r.name, r.lat, r.lon, r.maxspeed) for r in result.radars
    ]
    assert downloads == [] and reads == [] and asked == [madrid_multas.API]


def test_madrid_keeps_a_placed_limit_when_the_fines_change(monkeypatch):
    _fetch_fakes(monkeypatch, [], [])
    madrid_multas.SOURCE.fetch(CTX)
    path = madrid_multas._folder() / "places.json"
    kept = json.loads(path.read_text("utf-8"))
    assert kept["places"]["N001 PO MORET"]["limit"] == 50
    # February's summary now says 30 at Moret: the zone keeps its name and radius
    feb = madrid_multas._folder() / "2026-02.json"
    summary = json.loads(feb.read_text("utf-8"))
    summary["places"]["N001 PO MORET"]["limits"] = {"30": 99}
    feb.write_text(json.dumps(summary), "utf-8")
    radars = {r.name: r for r in madrid_multas.SOURCE.fetch(CTX).radars}
    assert radars["Radar móvil frecuente Paseo de Moret 1"].maxspeed == 50


def test_madrid_tries_a_skipped_place_again_only_with_a_new_register():
    calls = []

    def read(url, numbers):
        calls.append(url)
        return madrid_multas.read_register(lines(REGISTER), numbers)

    first = madrid_multas.place_all(["N009 AV JUAN DE HERRERA"], {}, ("u", "v1"), read)
    assert first["N009 AV JUAN DE HERRERA"] == {
        "skip": "no number 9 in the register",
        "register": "v1",
    }
    madrid_multas.place_all(["N009 AV JUAN DE HERRERA"], {"places": first}, ("u", "v1"), read)
    assert calls == ["u"]
    madrid_multas.place_all(["N009 AV JUAN DE HERRERA"], {"places": first}, ("u", "v2"), read)
    assert calls == ["u", "u"]

    # a placed place never moves: with a new place to read, it is not read again
    kept = {"street": "Calle de Josefa Valcárcel", "lat": 40.0, "lon": -3.0, "number": 128}
    numbers = []
    records = madrid_multas.place_all(
        ["N128 JOSEFA VALCARCEL", "N001 PO MORET"],
        {"places": {"N128 JOSEFA VALCARCEL": kept}},
        ("u", "v2"),
        lambda url, wanted: numbers.append(sorted(wanted)) or read(url, wanted),
    )
    assert records["N128 JOSEFA VALCARCEL"] == kept and numbers == [[1]]
    assert records["N001 PO MORET"]["street"] == "Paseo de Moret"


def test_madrid_reads_a_month_again_when_the_portal_replaces_it(monkeypatch):
    month = madrid_multas.Month(2026, 2, "https://x/feb.csv", "210104-379", "2026-10-02T07:52:11")
    reads = []

    def download(url):
        reads.append(url)
        return {"N001 PO MORET": {"fines": len(reads), "limits": {"50": 1}, "hours": [9]}}

    monkeypatch.setattr(madrid_multas, "_download", download)
    assert madrid_multas.month_places(month)["N001 PO MORET"]["fines"] == 1
    assert madrid_multas.month_places(month)["N001 PO MORET"]["fines"] == 1
    replaced = replace(month, modified="2026-11-02T07:00:00")
    assert madrid_multas.month_places(replaced)["N001 PO MORET"]["fines"] == 2
    # a summary kept by an older version of the source is read again
    path = madrid_multas._folder() / "2026-02.json"
    old = json.loads(path.read_text("utf-8")) | {"version": 1}
    path.write_text(json.dumps(old), "utf-8")
    assert madrid_multas.month_places(replaced)["N001 PO MORET"]["fines"] == 3


# ---- both: in the feed and in Home Assistant -------------------------------------


def _radar(source: str, kind: str, lat: float, name: str = "Radar") -> Radar:
    return Radar(
        id=f"{source}-{kind}-{lat}",
        source=source,
        kind=kind,
        name=name,
        lat=lat,
        lon=2.0,
        radius_m=500,
    )


@pytest.mark.parametrize("camera_source", ["osm", "dgt", "madrid"])
def test_a_camera_of_another_source_wins_over_a_fines_place(camera_source):
    camera = _radar(camera_source, "fixed", 41.0009)  # 100 m north
    spot = _radar("barcelona_multas", "mobile_recurring", 41.0)
    assert [r.id for r in feed.merge([spot, camera], DAY)] == [camera.id]
    away = _radar(camera_source, "fixed", 41.0018)  # 200 m north
    assert len(feed.merge([spot, away], DAY)) == 2
    # a mobile-radar stretch circle is no camera
    circle = _radar("dgt_invive", "mobile_stretch", 41.0009)
    assert len(feed.merge([spot, circle], DAY)) == 2


def test_a_fines_place_without_a_limit_is_sized_as_a_city_street():
    spot = _radar("barcelona_multas", "mobile_recurring", 41.0)
    assert speed.fallback_kmh(spot) == speed.URBAN_KMH
    assert speed.size([spot], Radius())[0].radius_m == 756


def test_over_the_cap_a_fines_spot_comes_after_fixed_radars_and_before_stretches():
    home = (41.0, 2.0)
    stretch = _radar("dgt_invive", "mobile_stretch", 41.001)  # nearest of all
    spot = _radar("madrid_multas", "mobile_recurring", 41.01)
    fixed = _radar("dgt", "fixed", 41.1)
    far = _radar("osm", "fixed", 45.0)
    radars = [stretch, spot, fixed, far]
    for cap, want in [(1, [fixed]), (2, [fixed, far]), (3, [fixed, far, spot])]:
        kept, left_out = ha.select(radars, cap, home)
        assert kept == want, cap
        assert left_out == len(radars) - cap


# ---- a camera source with no result yet ---------------------------------------


def _source(key, fetch, **kw):
    from radares_anunciados.sources import Source

    return Source(key, fetch, key, "licence", **kw)


@pytest.mark.parametrize(
    ("missing", "held"),
    [
        (frozenset({"osm"}), True),  # cameras anywhere: no fines spot this run
        (frozenset({"madrid"}), True),  # Madrid's cameras: the Madrid spot waits
        (frozenset({"euskadi"}), False),  # Basque cameras: nothing near Madrid
        (frozenset({"osm_notes"}), False),  # all of Spain, but no cameras: only reports
        (frozenset(), False),
    ],
)
def test_a_fines_spot_waits_while_a_camera_source_has_no_result(missing, held, caplog):
    spot = replace(_radar("madrid_multas", "mobile_recurring", 40.4), province="28")
    other = replace(_radar("madrid", "fixed", 41.4), province="28")
    with caplog.at_level("WARNING"):
        kept = feed.merge([spot, other], DAY, missing)
    assert (spot not in kept) is held and other in kept
    assert ("fines spot(s) held back" in caplog.text) is held


def test_collect_holds_fines_spots_until_the_camera_source_first_answers(monkeypatch, caplog):
    from radares_anunciados import cli, sources
    from radares_anunciados.model import SourceResult

    monkeypatch.setenv("RADARES_PROVINCES", "all")
    camera_up = {"now": False}

    def cameras(ctx):
        if not camera_up["now"]:
            raise OSError("Overpass 504")
        return SourceResult([replace(_radar("cams", "fixed", 45.0), province="28")])

    spot = replace(_radar("fines", "mobile_recurring", 40.4), province="28")
    registry = {
        "cams": _source("cams", cameras, cameras=True, official=False),
        "fines": _source("fines", lambda ctx: SourceResult([spot])),
    }
    monkeypatch.setattr(sources, "REGISTRY", registry)
    monkeypatch.setattr(feed, "REGISTRY", registry)
    with caplog.at_level("WARNING"):
        first = cli.collect(DAY, save_history=False)
    assert [r.id for r in first.radars] == []
    assert "1 fines spot(s) held back this run: cams gave no cameras yet" in caplog.text
    camera_up["now"] = True
    assert len(cli.collect(DAY, save_history=False).radars) == 2
    # failed again, but its last good result stands: the spot stays
    camera_up["now"] = False
    assert spot.id in {r.id for r in cli.collect(DAY, save_history=False).radars}
    # a camera source left out of RADARES_SOURCES holds nothing back
    monkeypatch.setenv("RADARES_SOURCES", "fines")
    assert [r.id for r in cli.collect(DAY, save_history=False).radars] == [spot.id]


def test_the_sources_that_publish_cameras_are_marked():
    from radares_anunciados import sources

    marked = sorted(k for k, s in sources.REGISTRY.items() if s.cameras)
    assert marked == [
        "dgt",
        "donostia",
        "euskadi",
        "madrid",
        "navarra",
        "osm",
        "salamanca",
        "sct",
        "sct_remolc",
    ]


# ---- boundaries the second review found unpinned ----------------------------------


def _short_days(start, n, gap=1):
    """``n`` days of one-hour sessions, ``gap`` days apart."""
    from datetime import timedelta

    return [Day(start + timedelta(days=i * gap), 5, 10.0, 11.0) for i in range(n)]


def test_barcelona_eight_days_in_a_row_is_a_camera_seven_is_not():
    assert barcelona_multas.camera_like(_short_days(date(2025, 10, 1), 8), 92) == (
        "fines on 8 days in a row"
    )
    assert barcelona_multas.camera_like(_short_days(date(2025, 10, 1), 7), 92) == ""


def test_barcelona_more_than_half_the_quarter_is_a_camera():
    # every other day: never two in a row, an hour each
    assert barcelona_multas.camera_like(_short_days(date(2025, 10, 1), 47, gap=2), 92) == (
        "fines on 47 of 92 days"
    )
    assert barcelona_multas.camera_like(_short_days(date(2025, 10, 1), 46, gap=2), 92) == ""


def _record(street, number, lat):
    return {"street": street, "key": [street], "number": number, "lat": lat, "lon": -3.7}


def test_madrid_merges_places_of_one_street_within_200_m_only():
    window = madrid_multas.months(fixture(PACKAGE))[-2:]
    places = {p: (2, Counter({"50": 2})) for p in ("N010 X", "N020 X", "N030 X", "N012 Y")}
    records = {
        "N010 X": _record("Calle X", 10, 40.4),
        "N020 X": _record("Calle X", 20, 40.4017),  # 189 m from 10: one spot
        "N030 X": _record("Calle X", 30, 40.40395),  # 250 m from 20: another spot
        "N012 Y": _record("Avenida Y", 12, 40.40009),  # another street, 10 m from 10
    }
    in_months = {p: {0, 1} for p in places}
    radars, skipped = madrid_multas.to_radars(window, places, records, in_months)
    assert skipped == []
    assert sorted(r.name for r in radars) == [
        "Radar móvil frecuente Avenida Y 12",
        "Radar móvil frecuente Calle X 10",
        "Radar móvil frecuente Calle X 30",
    ]


def test_madrid_needs_one_word_written_in_full():
    assert madrid_multas.same_street("J VALCARCEL", "calle", "josefa valcarcel") is True
    assert madrid_multas.same_street("J V", "calle", "josefa valcarcel") is False


def test_madrid_prefers_the_plain_number_and_its_portal():
    street = madrid_multas.Street("calle", "x", "Calle X")
    here, far = (40.4, -3.7), (40.403, -3.7)  # 333 m apart
    plain_first = {("calle", "x", 10): [("", "GARAJE", here), ("A", "PORTAL", far)]}
    assert madrid_multas.locate("N010 X", [street], plain_first) == (street, here)
    portal_first = {("calle", "x", 10): [("", "PORTAL", here), ("", "GARAJE", far)]}
    assert madrid_multas.locate("N010 X", [street], portal_first) == (street, here)
