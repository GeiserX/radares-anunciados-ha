"""The published feed: `radares feed --status`, the page and the data license."""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from radares_anunciados import cli, net, sources, store
from radares_anunciados.geocode import Geocoder, Town
from radares_anunciados.model import REPORTED, Radar, SourceResult, Stretch
from radares_anunciados.sources import Source

ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("RADARES_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RADARES_CACHE", str(tmp_path / "cache"))


def fake(key, answers, spanish_ip_hosts=frozenset()):
    def fetch(ctx):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return Source(key, fetch, f"{key} people", f"{key} licence", spanish_ip_hosts=spanish_ip_hosts)


def radar(source, n=0, kind="fixed", **kw):
    return Radar(f"{source}-{n}", source, kind, f"Radar {n}", 37.0 + n / 10, -1.0, 500, **kw)


def test_status_says_which_sources_are_fresh_stale_or_missing(monkeypatch):
    stretch = Stretch("ok-s", "ok", "Tramo", "A-7", (37.0, -1.0), (37.1, -1.1))
    registry = {
        "ok": fake(
            "ok",
            iter(
                [SourceResult([radar("ok"), radar("ok", 1)], [stretch], updated="2026-09-17")] * 2
            ),
        ),
        "notes": fake("notes", iter([SourceResult([radar("notes", kind=REPORTED)])] * 2)),
        "stale": fake("stale", iter([SourceResult([radar("stale", 5)]), OSError("HTTP 403")])),
        "missing": fake(
            "missing", iter([OSError("TLS handshake")] * 2), spanish_ip_hosts=frozenset({"x.es"})
        ),
    }
    monkeypatch.setattr(sources, "REGISTRY", registry)
    cli.collect(date(2026, 9, 28), save_history=False)  # "stale" succeeds once
    found = cli.collect(date(2026, 9, 28), save_history=False)
    report = cli.status(found, now=1_790_000_000.0)
    rows = {r["source"]: r for r in report["sources"]}
    assert report["generated"] == "2026-09-21T14:13:20+00:00"
    assert report["features"] == 5 and report["reported"] == 1
    notes = rows["notes"]
    assert (notes["radars"], notes["reported"], notes["in_feed"]) == (0, 1, 1)
    assert rows["ok"] | {"data_time": None} == {
        "source": "ok",
        "status": "ok",
        "radars": 2,
        "stretches": 1,
        "reported": 0,
        "in_feed": 3,
        "data_time": None,
        "updated": "2026-09-17",
        "error": "",
        "attribution": "ok people",
        "licence": "ok licence",
        "spanish_ip": False,
    }
    assert rows["ok"]["data_time"].endswith("+00:00")
    assert (rows["stale"]["status"], rows["stale"]["radars"], rows["stale"]["in_feed"]) == (
        "stale",
        1,
        1,
    )
    assert rows["stale"]["data_time"] and "403" in rows["stale"]["error"]
    assert rows["missing"] | {"error": ""} == {
        "source": "missing",
        "status": "missing",
        "radars": 0,
        "stretches": 0,
        "reported": 0,
        "in_feed": 0,
        "data_time": None,
        "updated": None,
        "error": "",
        "attribution": "missing people",
        "licence": "missing licence",
        "spanish_ip": True,
    }


def announced_this_week(source):
    monday = date.today() - timedelta(days=date.today().weekday())
    end = monday + timedelta(days=6)
    return [radar(source, valid_from=monday, valid_to=end)]


def test_feed_writes_the_feed_and_its_status(monkeypatch, tmp_path):
    answers = iter([SourceResult(announced_this_week("m"))] * 2)
    monkeypatch.setattr(sources, "REGISTRY", {"m": fake("m", answers)})
    out, status = tmp_path / "feed.geojson", tmp_path / "status.json"
    assert cli.main(["feed", "-o", str(out), "--status", str(status)]) == 0
    data = json.loads(out.read_text("utf-8"))
    report = json.loads(status.read_text("utf-8"))
    assert [f["id"] for f in data["features"]] == ["m-0"]
    assert report["features"] == 1 and report["sources"][0]["status"] == "ok"
    # a plain feed leaves the history alone; the published feed keeps its own
    assert store.load_announced() == []
    assert cli.main(["feed", "-o", str(out), "--save-history"]) == 0
    assert [r.id for r in store.load_announced()] == ["m-0"]


def test_an_empty_feed_still_writes_a_valid_status(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "REGISTRY", {"m": fake("m", iter([OSError("down")]))})
    out, status = tmp_path / "feed.geojson", tmp_path / "status.json"
    assert cli.main(["feed", "-o", str(out), "--status", str(status)]) == 0
    assert json.loads(out.read_text("utf-8")) == {"type": "FeatureCollection", "features": []}
    report = json.loads(status.read_text("utf-8"))
    assert report["features"] == 0 and report["sources"][0]["status"] == "missing"


def kept_between_runs() -> list[list[str]]:
    """The paths of each actions/cache step in feed.yml that keeps the sources' last good
    results, relative to the cache folder. The road geometry has its own entry (roads/)."""
    lines = (ROOT / ".github" / "workflows" / "feed.yml").read_text("utf-8").splitlines()
    blocks = []
    for i, line in enumerate(lines):
        if line.strip() == "path: |":
            indent = len(line) - len(line.lstrip()) + 2
            block = []
            for nxt in lines[i + 1 :]:
                if len(nxt) - len(nxt.lstrip()) < indent or not nxt.strip():
                    break
                block.append(nxt.strip())
            blocks.append(block)
        elif re.match(r"\s*path: .*radares-cache", line):
            blocks.append([line.split("path:", 1)[1].strip()])
    prefix = "${{ runner.temp }}/radares-cache"
    blocks = [[p.removeprefix(prefix).lstrip("/") for p in block] for block in blocks]
    return [b for b in blocks if b != ["roads"]]


def test_the_road_geometry_is_kept_between_runs_under_its_own_key():
    """roads.json is put back when the roads step fails. Its cache entry has its own key and
    path: actions/cache matches an entry only on an identical path list, so adding roads/ to
    the sources' list would hide their last good results for one run."""
    steps = feed_jobs()["publish"]["steps"]
    roads = [s for s in steps if "roads-state-" in str(s.get("with", {}).get("key", ""))]
    assert [s["uses"].split("@")[0] for s in roads] == [
        "actions/cache/restore",
        "actions/cache/save",
    ]
    restore, save = roads
    folder = "${{ runner.temp }}/radares-cache/roads"
    assert restore["with"]["path"] == save["with"]["path"] == folder
    assert restore["with"]["restore-keys"] == "roads-state-"
    assert save["if"] == "always()"
    names = [s.get("name") for s in steps]
    assert names.index(restore["name"]) < names.index("Build the feed") < names.index(save["name"])
    # the build's roads step refreshes and falls back on exactly that folder
    action = yaml.safe_load(
        (ROOT / ".github" / "actions" / "build-feed" / "action.yml").read_text("utf-8")
    )
    step = next(s for s in action["runs"]["steps"] if s.get("name") == "Follow the roads")
    assert '--fallback "$RADARES_CACHE/roads"' in step["run"]
    assert step["env"]["RADARES_CACHE"] == "${{ inputs.cache }}"


def test_a_scheduled_run_never_calls_a_source_ok_from_a_download_it_did_not_make(
    monkeypatch, tmp_path
):
    """The published run keeps only what the fallback needs. A download younger than its
    max age is reused without a request; kept between runs, it would make a source whose
    site is down look `ok`, with this run's time, in status.json."""
    blocks = kept_between_runs()
    assert len(blocks) == 2 and blocks[0] == blocks[1], "restore and save must keep the same paths"
    kept = blocks[0]
    assert "" not in kept, "the whole cache folder, downloads included, is kept between runs"

    calls = []

    def get(url, data=None, headers=None):
        calls.append(url)
        if len(calls) > 1:
            raise OSError("HTTP 503")
        return b'{"n": 2}'

    def fetch(ctx):
        n = json.loads(net.cached_get("https://example.org/radars"))["n"]
        # a list street the map missed is asked of the geocoders, whose answers are kept
        Geocoder(Town("Up", "00000", (0, 0, 1, 1))).street("Calle Nueva", None, [])
        return SourceResult([radar("up", i) for i in range(n)])

    monkeypatch.setattr(net, "get", get)
    monkeypatch.setattr(Geocoder, "_get", lambda self, provider, url, params: [])
    up = Source("up", fetch, "up people", "up licence")
    monkeypatch.setattr(sources, "REGISTRY", {"up": up})
    first = cli.status(cli.collect(date(2026, 9, 28), save_history=True), now=1_790_000_000.0)
    assert first["sources"][0]["status"] == "ok"

    # the next scheduled run starts on a fresh runner with only the kept paths restored
    old, new = tmp_path / "cache", tmp_path / "next-run"
    for rel in kept:
        src = old / rel
        assert src.exists(), f"{rel} is kept between runs but the feed never writes it"
        dst = new / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    monkeypatch.setenv("RADARES_CACHE", str(new))
    second = cli.status(cli.collect(date(2026, 9, 28), save_history=True), now=1_790_021_600.0)
    row = second["sources"][0]
    assert len(calls) == 2, "the next run must ask the source again"
    assert (row["status"], row["radars"], "503" in row["error"]) == ("stale", 2, True)
    assert row["data_time"] == first["sources"][0]["data_time"]


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_the_page_loads_only_pinned_files_with_an_integrity_hash():
    parser = Tags()
    parser.feed((ROOT / "site" / "index.html").read_text("utf-8"))
    external = [
        (tag, a)
        for tag, a in parser.tags
        if tag in ("script", "link") and (a.get("src") or a.get("href") or "").startswith("http")
    ]
    assert external, "the page loads its map library from a CDN"
    for _, a in external:
        url = a.get("src") or a["href"]
        assert re.search(r"@\d+\.\d+\.\d+/", url), f"{url} is not pinned to an exact version"
        assert re.fullmatch(r"sha(384|512)-[A-Za-z0-9+/=]{64,}", a.get("integrity", "")), url
        assert a.get("crossorigin") == "anonymous", url
    metas = [a for tag, a in parser.tags if tag == "meta"]
    csp = next(a["content"] for a in metas if a.get("http-equiv") == "Content-Security-Policy")
    directives = {}
    for part in filter(None, (p.strip() for p in csp.split(";"))):
        name, *values = part.split()
        directives[name] = set(values)
    # No tracking and no external fonts: the page, the pinned Leaflet files and OSM tiles only.
    # Anything not listed (font-src, frame-src...) falls back to default-src 'none'.
    assert directives == {
        "default-src": {"'none'"},
        "script-src": {"'self'", "https://unpkg.com"},
        "style-src": {"'self'", "https://unpkg.com"},
        "img-src": {"'self'", "data:", "https://tile.openstreetmap.org"},
        "connect-src": {"'self'"},
        "base-uri": {"'none'"},
        "form-action": {"'none'"},
    }


def test_the_data_license_credits_every_registered_source():
    text = (ROOT / "LICENSE-DATA.md").read_text("utf-8")
    for key in sources.REGISTRY:
        assert f"| `{key}` |" in text, f"LICENSE-DATA.md has no row for the {key} source"


def test_every_source_row_in_the_data_license_names_its_attribution_and_terms():
    rows = {}
    for line in (ROOT / "LICENSE-DATA.md").read_text("utf-8").splitlines():
        if m := re.match(r"\| `(\w+)` \|", line):
            rows[m.group(1)] = [c.strip() for c in line.strip().strip("|").split("|")]
    for key in sources.REGISTRY:
        cells = rows.get(key)
        assert cells is not None, f"LICENSE-DATA.md has no row for the {key} source"
        # | Source | What it gives | Attribution | Terms |
        assert len(cells) == 4, f"the {key} row has {len(cells)} cells, not 4"
        assert cells[2], f"the {key} row names no attribution"
        assert cells[3], f"the {key} row names no terms"


WORKFLOWS = ROOT / ".github" / "workflows"


def feed_jobs() -> dict:
    return yaml.safe_load((WORKFLOWS / "feed.yml").read_text("utf-8"))["jobs"]


def test_only_the_publish_job_targets_the_self_hosted_runner():
    """The publishing job runs on a self-hosted runner in Spain, so it may run only on
    events a fork cannot trigger. Every other job, in every workflow, stays on a
    GitHub-hosted runner, and none runs a pull request's code with pull_request_target.
    (A pull request can still edit these files; the approval of outside contributors'
    runs is what stops that, see AGENTS.md.)"""
    files = sorted(WORKFLOWS.glob("*.y*ml"))
    assert len(files) >= 4
    hosted = []
    for path in files:
        text = path.read_text("utf-8")
        spec = yaml.safe_load(text)
        triggers = spec.get("on", spec.get(True))  # PyYAML reads a bare `on` as True
        assert "pull_request_target" not in str(triggers), f"{path.name} uses pull_request_target"
        for name, job in spec["jobs"].items():
            if "self-hosted" in str(job.get("runs-on")):
                hosted.append((path.name, name))
    assert hosted == [("feed.yml", "publish")]
    jobs = feed_jobs()
    trusted = "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert jobs["publish"]["if"] == trusted
    assert jobs["check"]["if"] == "github.event_name == 'pull_request'"
    assert jobs["check"]["runs-on"] == "ubuntu-latest"
    # deploy publishes only what publish built, so it runs only when publish did
    assert jobs["deploy"]["needs"] == "publish"
    assert jobs["deploy"]["runs-on"] == "ubuntu-latest"


def test_the_publish_job_clears_the_cache_folder_before_restoring_it():
    """The self-hosted runner is not wiped between runs: the downloads of the last run
    must go before the restore, or a source would look `ok` while its site is down."""
    steps = feed_jobs()["publish"]["steps"]
    names = [step.get("name") for step in steps]
    clear = steps[names.index("Clear the cache folder")]
    restore = steps[names.index("Restore the last good results")]
    assert names.index("Clear the cache folder") < names.index("Restore the last good results")
    folder = "${{ runner.temp }}/radares-cache"
    assert clear["run"].strip() == f'rm -rf "{folder}"'
    assert all(p.startswith(folder + "/") for p in restore["with"]["path"].splitlines())
    build = steps[names.index("Build the feed")]
    assert build["with"]["cache"] == folder
    # The OSM extract (1.5 GB) must survive between runs: never under runner.temp, which the
    # runner wipes, and never under the folder this step deletes.
    cleared = clear["run"].strip().removeprefix("rm -rf ").strip('"')
    assert cleared.startswith("${{ runner.temp }}/")
    pbf = build["with"]["pbf"]
    assert pbf == "${{ runner.workspace }}/../radares-pbf/spain-latest.osm.pbf"
    assert not pbf.startswith(cleared) and "runner.temp" not in pbf
    refresh = steps[names.index("Refresh the OpenStreetMap extract")]
    assert names.index("Refresh the OpenStreetMap extract") < names.index("Build the feed")
    assert refresh["env"]["PBF_DIR"] == "${{ runner.workspace }}/../radares-pbf"
    assert refresh["continue-on-error"] is True  # a failed download never stops the feed


def report_step() -> str:
    """The Python of the composite action's "Report the sources" step."""
    action = yaml.safe_load(
        (ROOT / ".github" / "actions" / "build-feed" / "action.yml").read_text()
    )
    step = next(s for s in action["runs"]["steps"] if s.get("name") == "Report the sources")
    return step["run"].split("<<'PY'\n", 1)[1].rsplit("PY\n", 1)[0]


@pytest.mark.parametrize("features, code", [(0, 1), (1, 0)])
def test_the_build_refuses_to_publish_an_empty_feed(tmp_path, features, code):
    site = tmp_path / "_site"
    site.mkdir()
    status = {
        "features": features,
        "sources": [
            {
                "source": "dgt",
                "status": "ok",
                "radars": features,
                "stretches": 0,
                "in_feed": features,
                "data_time": "2026-10-02T00:00:00+00:00",
                "updated": "2025-12-18",
                "error": "a | b\nc",
            }
        ],
    }
    feed = {"type": "FeatureCollection", "features": [{"type": "Feature"}] * features}
    (site / "status.json").write_text(json.dumps(status), "utf-8")
    (site / "feed.geojson").write_text(json.dumps(feed), "utf-8")
    summary = tmp_path / "summary.md"
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary)}
    run = subprocess.run(
        [sys.executable, "-c", report_step()], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert run.returncode == code, run.stdout + run.stderr
    assert ("::error::the feed has no features" in run.stdout) == (code == 1)
    assert "| dgt | ok |" in summary.read_text("utf-8") and "a   b c" in summary.read_text("utf-8")


def test_only_the_publish_job_downloads_the_osm_extract():
    """The pull request job runs the roads step on the committed clip; Geofabrik is reached
    only by the scheduled publish, once a week."""
    jobs = feed_jobs()
    assert "geofabrik" in str(jobs["publish"]).lower()
    assert "geofabrik" not in str(jobs["check"]).lower()
    build = next(s for s in jobs["check"]["steps"] if s.get("name") == "Build the feed")
    assert build["with"]["pbf"] == "tests/fixtures/roads/clip.osm.pbf"
    assert (ROOT / build["with"]["pbf"]).exists()


def test_the_image_never_installs_the_roads_group():
    """pyosmium is for the published feed only; Home Assistant users never download it."""
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert "default-groups" not in pyproject
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    syncs = [line for line in dockerfile.splitlines() if "uv sync" in line]
    assert syncs and all("--no-dev" in line for line in syncs)
    assert not any("--group" in line or "--all-groups" in line for line in syncs)
    action = (ROOT / ".github" / "actions" / "build-feed" / "action.yml").read_text("utf-8")
    assert "uv sync --locked --no-dev --group roads" in action


def test_the_pull_request_build_gives_up_fast_on_spanish_only_sources(monkeypatch):
    jobs = feed_jobs()
    assert int(jobs["check"]["env"]["RADARES_SPANISH_IP_TIMEOUT"]) <= 30
    assert "RADARES_SPANISH_IP_TIMEOUT" not in str(jobs["publish"])

    seen = {}

    def fetch(key):
        def run(ctx):
            # a Spain-only council site, and Overpass, which answers anywhere
            for url in ("https://www.ayto.es/radares", "https://overpass-api.de/api"):
                seen[key, url.split("/")[2]] = net.timeout_s(90, url)
            raise OSError("timed out")

        return run

    spain = frozenset({"ayto.es"})
    monkeypatch.setattr(
        sources,
        "REGISTRY",
        {
            "spain": Source("spain", fetch("spain"), "a", "l", spanish_ip_hosts=spain),
            "abroad": Source("abroad", fetch("abroad"), "a", "l"),
        },
    )
    monkeypatch.setenv("RADARES_SPANISH_IP_TIMEOUT", "20")
    rows = cli.status(cli.collect(date(2026, 9, 28), save_history=False), now=1_790_000_000.0)
    assert seen == {
        ("spain", "www.ayto.es"): 20,
        ("spain", "overpass-api.de"): 90,
        ("abroad", "www.ayto.es"): 90,
        ("abroad", "overpass-api.de"): 90,
    }
    assert {r["source"]: r["status"] for r in rows["sources"]} == {
        "spain": "missing",
        "abroad": "missing",
    }
    monkeypatch.delenv("RADARES_SPANISH_IP_TIMEOUT")
    cli.collect(date(2026, 9, 28), save_history=False)
    assert set(seen.values()) == {90}


def test_the_build_summary_reports_the_roads_block(tmp_path):
    site = tmp_path / "_site"
    site.mkdir()
    roads = {
        "file": "roads.json",
        "status": "stale",
        "osm_date": "2026-10-20",
        "stretches_followed": 1313,
        "points_with_approaches": 2233,
        "bytes": 810_310,
        "error": "RoadsError: only 1 of 9 radars got an approach",
    }
    status = {"features": 1, "sources": [], "roads": roads}
    feed = {"type": "FeatureCollection", "features": [{"type": "Feature"}]}
    (site / "status.json").write_text(json.dumps(status), "utf-8")
    (site / "feed.geojson").write_text(json.dumps(feed), "utf-8")
    summary = tmp_path / "summary.md"
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary)}
    run = subprocess.run(
        [sys.executable, "-c", report_step()], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stdout + run.stderr
    text = summary.read_text("utf-8")
    assert "roads.json: stale, 810,310 bytes, OSM data of 2026-10-20" in text
    assert "stretches_followed 1313, points_with_approaches 2233" in text
    assert "roads error: RoadsError: only 1 of 9" in text
