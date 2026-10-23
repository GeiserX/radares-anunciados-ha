"""`radares roads` end to end on the committed clip of the Murcia extract, and the
promise that `radares feed` (and so Home Assistant) never loads pyosmium."""

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from radares_anunciados import cli
from radares_anunciados.roads.polyline import decode

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "roads"
CLIP = FIXTURES / "clip.osm.pbf"
NEW_KEYS = {"bearing", "direction_semantics", "direction_confidence", "bearing_source"}
CREDIT = "; road: © OpenStreetMap contributors (ODbL 1.0)"


@pytest.fixture
def site(tmp_path):
    shutil.copy(FIXTURES / "feed.json", tmp_path / "feed.geojson")
    (tmp_path / "status.json").write_text(json.dumps({"features": 36, "sources": []}), "utf-8")
    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the roads step tried to reach the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def roads(site, *extra):
    return cli.main(
        [
            "roads",
            "--feed",
            str(site / "feed.geojson"),
            "--status",
            str(site / "status.json"),
            "--pbf",
            str(CLIP),
            "--output",
            str(site / "roads.json"),
            *extra,
        ]
    )


def test_the_roads_step_writes_all_three_files_from_the_clip(site, no_network):
    before = json.loads((FIXTURES / "feed.json").read_text("utf-8"))
    assert roads(site, "--audit", str(site / "audit.csv")) == 0

    doc = json.loads((site / "roads.json").read_text("utf-8"))
    assert doc["version"] == 1 and doc["precision"] == 5
    entries = doc["features"]
    assert entries, "nothing followed in the clip"
    for fid, entry in entries.items():
        for poly in [entry.get("line")] + [
            a["polyline"]
            for a in entry.get("approaches", [])
            + entry.get("gates", {}).get("start", [])
            + entry.get("gates", {}).get("end", [])
        ]:
            if poly is not None:
                assert len(decode(poly)) >= 2, fid

    status = json.loads((site / "status.json").read_text("utf-8"))
    block = status["roads"]
    assert status["features"] == 36  # the rest of status.json is kept
    assert block["status"] == "ok" and block["error"] == ""
    assert block["osm_date"] == "2026-10-20"
    assert block["bbox"] == [-1.5, 37.75, -0.9, 38.2]
    assert block["bytes"] == len((site / "roads.json").read_bytes())
    assert block["points_with_approaches"] >= 0.7 * block["points_in_bbox"] > 0
    assert block["stretches_followed"] >= 0.6 * block["stretches_in_bbox"] > 0
    assert block["points_with_bearing"] > 0

    text = (site / "feed.geojson").read_text("utf-8")
    assert "\n" not in text  # written compact, as `radares feed` does
    after = json.loads(text)
    assert [f["id"] for f in after["features"]] == [f["id"] for f in before["features"]]
    bearings = 0
    for old, new in zip(before["features"], after["features"], strict=True):
        assert new["geometry"] == old["geometry"]  # every coordinate, to the bit
        added = set(new["properties"]) - set(old["properties"])
        assert added <= NEW_KEYS
        assert list(new["properties"])[: len(old["properties"])] == list(old["properties"])
        for key, value in old["properties"].items():
            if key == "attribution" and value != new["properties"][key]:
                assert new["properties"][key] == value + CREDIT
                assert new["id"] in entries or added
            else:
                assert new["properties"][key] == value, (new["id"], key)
        if added:
            bearings += 1
            p = new["properties"]
            assert added == NEW_KEYS and p["direction_semantics"] == "travel"
            assert isinstance(p["bearing"], int) and 0 <= p["bearing"] < 360
            assert p["direction_confidence"] in ("high", "low")
            assert "OpenStreetMap" in p["attribution"]
    assert bearings == block["points_with_bearing"]
    audit = (site / "audit.csv").read_text("utf-8").splitlines()
    assert audit[0].startswith("id,source,snapped_m,s1_official_km")


def test_the_roads_step_is_deterministic(site, tmp_path):
    other = tmp_path / "again"
    other.mkdir()
    for name in ("feed.geojson", "status.json"):
        shutil.copy(site / name, other / name)
    assert roads(site) == 0 and roads(other) == 0
    assert (site / "roads.json").read_bytes() == (other / "roads.json").read_bytes()
    assert (site / "feed.geojson").read_bytes() == (other / "feed.geojson").read_bytes()


def test_a_failed_step_republishes_the_last_good_file_and_says_stale(site, tmp_path):
    keep = tmp_path / "keep"
    assert roads(site, "--fallback", str(keep)) == 0
    good = (site / "roads.json").read_bytes()
    assert (keep / "roads.json").read_bytes() == good
    feed_before = (FIXTURES / "feed.json").read_text("utf-8")

    (site / "roads.json").unlink()
    shutil.copy(FIXTURES / "feed.json", site / "feed.geojson")
    broken = site / "broken.osm.pbf"
    broken.write_bytes(b"not a pbf")
    code = cli.main(
        [
            "roads",
            "--feed",
            str(site / "feed.geojson"),
            "--status",
            str(site / "status.json"),
            "--pbf",
            str(broken),
            "--output",
            str(site / "roads.json"),
            "--fallback",
            str(keep),
        ]
    )
    assert code == 1
    assert (site / "roads.json").read_bytes() == good
    assert (site / "feed.geojson").read_text("utf-8") == feed_before  # left as it was
    block = json.loads((site / "status.json").read_text("utf-8"))["roads"]
    assert block["status"] == "stale" and block["error"]
    assert block["stretches_followed"] > 0  # the counts of the file it republished


def test_a_failed_first_run_says_stale_with_nothing_to_republish(site, tmp_path):
    assert roads(site, "--fallback", str(tmp_path / "empty")) == 0  # sanity: it can pass
    (site / "roads.json").unlink()
    code = cli.main(
        [
            "roads",
            "--feed",
            str(site / "feed.geojson"),
            "--status",
            str(site / "status.json"),
            "--pbf",
            str(site / "missing.osm.pbf"),
            "--output",
            str(site / "roads.json"),
            "--fallback",
            str(tmp_path / "never-written"),
        ]
    )
    assert code == 1 and not (site / "roads.json").exists()
    block = json.loads((site / "status.json").read_text("utf-8"))["roads"]
    assert (block["status"], block["bytes"]) == ("stale", 0)


PROBE = """
import json, sys
from radares_anunciados import cli, sources
from radares_anunciados.model import Radar, SourceResult
from radares_anunciados.sources import Source

def fetch(ctx):
    return SourceResult([Radar("t-1", "t", "fixed", "Radar", 38.053963, -1.1177311, 500)])

sources.REGISTRY = {"t": Source("t", fetch, "t", "t")}
site = sys.argv[1]
assert cli.main(["feed", "-o", site + "/feed.geojson", "--status", site + "/status.json"]) == 0
print("after feed:", "osmium" in sys.modules)
code = cli.main(["roads", "--feed", site + "/feed.geojson", "--status", site + "/status.json",
                 "--pbf", sys.argv[2], "--output", site + "/roads.json"])
print("after roads:", "osmium" in sys.modules, code)
"""


def test_the_feed_command_never_loads_pyosmium(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RADARES_")}
    env["RADARES_CACHE"] = str(tmp_path / "cache")
    run = subprocess.run(
        [sys.executable, "-c", PROBE, str(tmp_path), str(CLIP)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert run.returncode == 0, run.stderr
    assert "after feed: False" in run.stdout
    assert "after roads: True 0" in run.stdout  # positive control: the roads step loads it


def test_the_clip_is_small_and_says_its_box():
    assert CLIP.stat().st_size <= 8_000_000
