"""feed.geojson is written without whitespace and keeps every geometry exactly."""

import json
from pathlib import Path

from radares_anunciados import feed
from radares_anunciados.model import Radar, Stretch
from radares_anunciados.sources import dgt

FIX = Path(__file__).parent / "fixtures"
RADARS = dgt.parse_all((FIX / "dgt_radares.xml").read_bytes(), None)
EXTRA = [
    Radar(
        id="madrid-1",
        source="madrid",
        kind="fixed",
        name="Radar fijo M-30: túnel",
        lat=40.123456789012345,
        lon=-3.1,
        radius_m=200,
        attribution="Ayuntamiento de Madrid: CC BY 4.0",
    )
]


def compact(text: str) -> str:
    return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))


def build() -> str:
    return feed.to_geojson(RADARS.radars + EXTRA, RADARS.stretches)


def test_the_feed_is_written_minified():
    text = build()
    assert text == compact(text)
    assert "\n" not in text
    assert "túnel" in text  # not escaped to \\u00fa
    assert feed.dumps({"a": [1, 2]}) == '{"a":[1,2]}'


def test_the_pretty_form_fails_the_same_check():
    pretty = json.dumps(json.loads(build()), ensure_ascii=False, indent=1)
    assert pretty != compact(pretty)


def test_every_geometry_round_trips_to_the_bit():
    doc = json.loads(build())
    assert doc["type"] == "FeatureCollection"
    points = {f["id"]: f["geometry"] for f in doc["features"] if f["geometry"]["type"] == "Point"}
    for r in RADARS.radars + EXTRA:
        assert points[r.id] == {"type": "Point", "coordinates": [r.lon, r.lat]}
    lines = {f["id"]: f for f in doc["features"] if f["geometry"]["type"] == "LineString"}
    for s in RADARS.stretches:
        assert lines[s.id]["geometry"]["coordinates"] == [
            [s.start[1], s.start[0]],
            [s.end[1], s.end[0]],
        ]
        # a section's ends sit bit for bit on its line's ends (the app folds them)
        assert points[f"{s.id}-from"]["coordinates"] == lines[s.id]["geometry"]["coordinates"][0]
        assert points[f"{s.id}-to"]["coordinates"] == lines[s.id]["geometry"]["coordinates"][-1]
    assert len(doc["features"]) == len(RADARS.radars) + len(EXTRA) + len(RADARS.stretches)


def test_a_followed_line_keeps_its_vertices():
    s = Stretch(
        id="s", source="dgt_invive", name="Tramo", road="N-1", start=(1.0, 2.0), end=(1.2, 2.2),
        line=((1.0, 2.0), (1.1, 2.15), (1.2, 2.2)),
    )  # fmt: skip
    (feature,) = json.loads(feed.to_geojson([], [s]))["features"]
    assert feature["geometry"]["coordinates"] == [[2.0, 1.0], [2.15, 1.1], [2.2, 1.2]]
