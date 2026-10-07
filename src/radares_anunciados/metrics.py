"""Prometheus metrics and a health check for ``radares run``, standard library only.

Three failures used to be log lines only, and each means driving past a radar
with no warning: a street that can't be placed on the map, a week without a
list, and runs that keep failing while the container looks fine. ``State``
holds what the last runs did; ``serve`` publishes it on ``/metrics`` (Prometheus
text format) and ``/healthz`` (503 once the last success is older than
``STALE_RUNS`` intervals). docs/alerting.md has the alert rules.
"""

from __future__ import annotations

import http.client
import os
import threading
import time
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .model import SPAIN, Radar
from .streets import WeeklyList

DEFAULT_PORT = 9464
STALE_RUNS = 3  # /healthz fails once the last success is this many intervals old


def port_from_env() -> int | None:
    """RADARES_METRICS_PORT; unset means the default, empty or 0 turns the server off.
    Anything else that is not a port raises ValueError."""
    raw = os.environ.get("RADARES_METRICS_PORT")
    if raw is None:
        return DEFAULT_PORT
    try:
        port = int(raw) if raw.strip() else 0
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise ValueError(f"RADARES_METRICS_PORT={raw!r} is not a port")
    return port or None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _sample(name: str, labels: dict[str, str], value: float) -> str:
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
    number = str(value) if isinstance(value, int) else repr(float(value))
    return f"{name}{{{inner}}} {number}" if inner else f"{name} {number}"


def _midnight_utc(day) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


def _midnight_spain(day) -> float:
    # The week rolls over on Spain's date (model.today_in_spain), so its start does too.
    return datetime(day.year, day.month, day.day, tzinfo=SPAIN).timestamp()


class State:
    """What the run loop did, read by the HTTP thread. Every write holds the lock."""

    def __init__(self, interval_s: int, now: float | None = None):
        self._lock = threading.Lock()
        self.interval_s = interval_s
        self.started = time.time() if now is None else now
        self.last_run: float | None = None
        self.last_success: float | None = None
        self.failed_runs = 0
        self.radars: dict[str, int] = {}
        self.zones: dict[str, int] = {}
        self.left_out: int | None = None
        self.lists: list[WeeklyList] = []
        # source -> (fetched this run, when the data in use was fetched or None)
        self.sources: dict[str, tuple[bool, float | None]] = {}

    def collected(
        self,
        radars: list[Radar],
        lists: list[WeeklyList],
        sources: dict[str, tuple[bool, float | None]] | None = None,
    ) -> None:
        """The sources ran: count the radars, keep the weekly lists' status and
        whether each source answered."""
        with self._lock:
            # A weekly source with no list this week counts 0, not absent.
            self.radars = {w.source: 0 for w in lists} | Counter(r.source for r in radars)
            self.lists = list(lists)
            if sources is not None:
                self.sources = dict(sources)

    def synced(
        self, keep: int, created: int, deleted: int, updated: int = 0, left_out: int = 0
    ) -> None:
        with self._lock:
            self.zones = {"kept": keep, "created": created, "deleted": deleted, "updated": updated}
            self.left_out = left_out

    def finished(self, ok: bool, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self.last_run = now
            if ok:
                self.last_success = now
                self.failed_runs = 0
            else:
                self.failed_runs += 1

    def health(self, now: float | None = None) -> tuple[bool, str]:
        """Healthy until the last success (or the start, before any) is too old."""
        now = time.time() if now is None else now
        limit = STALE_RUNS * self.interval_s
        with self._lock:
            since, what = (
                (self.last_success, "last success")
                if self.last_success is not None
                else (self.started, "no success yet, started")
            )
            failed = self.failed_runs
        age = now - since
        text = f"{what} {age:.0f} s ago, limit {limit} s, {failed} failed runs in a row"
        return age <= limit, ("ok: " if age <= limit else "stale: ") + text

    def render(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        out: list[str] = []

        def metric(name: str, kind: str, doc: str, samples: list) -> None:
            if not samples:
                return
            out.append(f"# HELP {name} {doc}")
            out.append(f"# TYPE {name} {kind}")
            out.extend(_sample(name, labels, value) for labels, value in samples)

        with self._lock:
            metric(
                "radares_build_info",
                "gauge",
                "Version of radares-anunciados-ha; always 1.",
                [({"version": __version__}, 1)],
            )
            metric(
                "radares_interval_seconds",
                "gauge",
                "Seconds between runs (RADARES_INTERVAL).",
                [({}, self.interval_s)],
            )
            metric(
                "radares_last_run_timestamp_seconds",
                "gauge",
                "When the last run ended, failed or not.",
                [({}, self.last_run)] if self.last_run is not None else [],
            )
            metric(
                "radares_last_success_timestamp_seconds",
                "gauge",
                "When the last run that synced Home Assistant ended.",
                [({}, self.last_success)] if self.last_success is not None else [],
            )
            metric(
                "radares_consecutive_failed_runs",
                "gauge",
                "Runs failed in a row; Home Assistant keeps the previous zones meanwhile.",
                [({}, self.failed_runs)],
            )
            metric(
                "radares_radars",
                "gauge",
                "Radars per source in the last collected list.",
                [({"source": s}, n) for s, n in sorted(self.radars.items())],
            )
            metric(
                "radares_sync_zones",
                "gauge",
                "Radar zones kept, created, deleted and updated (icon only) by the last sync.",
                [({"action": a}, n) for a, n in self.zones.items()],
            )
            metric(
                "radares_zones_left_out",
                "gauge",
                "Radars without a zone because there were more than RADARES_MAX_ZONES.",
                [({}, self.left_out)] if self.left_out is not None else [],
            )
            metric(
                "radares_source_up",
                "gauge",
                "1 if the source answered in the last run, 0 if it failed and its last "
                "good result (if any) was used.",
                [({"source": k}, int(up)) for k, (up, _) in sorted(self.sources.items())],
            )
            metric(
                "radares_source_data_age_seconds",
                "gauge",
                "Age of the source data in use; grows while the source keeps failing.",
                [
                    ({"source": k}, max(0.0, now - at))
                    for k, (_, at) in sorted(self.sources.items())
                    if at is not None
                ],
            )
            metric(
                "radares_weekly_list_found",
                "gauge",
                "1 if this week's police list was found, 0 if not published or not found.",
                [({"source": w.source}, int(w.published is not None)) for w in self.lists],
            )
            metric(
                "radares_weekly_list_missing_seconds",
                "gauge",
                "Seconds since this week started (Monday 00:00, Spanish time) with no police "
                "list found; 0 once found.",
                [
                    (
                        {"source": w.source},
                        0 if w.published is not None else max(0, now - _midnight_spain(w.week)),
                    )
                    for w in self.lists
                ],
            )
            metric(
                "radares_weekly_list_published_timestamp_seconds",
                "gauge",
                "Publication day (midnight UTC) of this week's police list.",
                [
                    ({"source": w.source}, _midnight_utc(w.published))
                    for w in self.lists
                    if w.published is not None
                ],
            )
            metric(
                "radares_weekly_list_streets",
                "gauge",
                "Streets announced in this week's police list.",
                [({"source": w.source}, len(w.streets)) for w in self.lists],
            )
            metric(
                "radares_weekly_list_streets_skipped",
                "gauge",
                "Announced streets that could not be placed on the map: no zone, no warning.",
                [({"source": w.source}, len(w.skipped)) for w in self.lists],
            )
            metric(
                "radares_street_skipped",
                "gauge",
                "1 for each announced street that could not be placed on the map.",
                [
                    ({"source": w.source, "street": s.street, "place": s.place or ""}, 1)
                    for w in self.lists
                    for s in w.skipped
                ],
            )
        return "\n".join(out) + "\n"


def serve(state: State, port: int, host: str = "") -> ThreadingHTTPServer:
    """Serve /metrics and /healthz from a daemon thread. Port 0 picks a free one."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/metrics":
                code, ctype, body = 200, "text/plain; version=0.0.4", state.render()
            elif path == "/healthz":
                ok, text = state.health()
                code, ctype, body = (200 if ok else 503), "text/plain", text + "\n"
            else:
                code, ctype, body = 404, "text/plain", "not found\n"
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args) -> None:
            pass  # a scrape every 15 s would drown the run log

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="metrics", daemon=True).start()
    return server


def check(port: int | None, timeout: float = 5) -> int:
    """Exit code for the container health check: 0 if /healthz says ok.

    A metrics server that could not start fails the check: nothing answers, or
    whatever holds the port does not answer "ok:". The sync keeps running, and
    the unhealthy container is how someone notices."""
    if port is None:
        return 0  # metrics off: nothing to ask, so don't mark the container unhealthy
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace").strip()
            print(body)
            return 0 if body.startswith("ok: ") else 1
    # HTTPError (503) is an OSError too; HTTPException is a port held by something not HTTP.
    except (OSError, http.client.HTTPException) as exc:
        body = exc.read().decode("utf-8", "replace").strip() if hasattr(exc, "read") else ""
        print(body or f"health check failed: {exc}")
        return 1
