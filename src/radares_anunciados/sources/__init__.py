"""Every source, registered in one place, and the one way to run them.

To add a source: write ``sources/<key>.py`` with ``SOURCE = Source(...)`` (see
``base.py`` for the contract), import it below and add it to ``REGISTRY``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, replace
from datetime import date

from .. import net, store
from ..model import SourceResult, WeeklyList
from . import (
    barcelona_multas,
    dgt,
    dgt_freshness,
    dgt_invive,
    donostia,
    donostia_movil,
    euskadi,
    leon,
    madrid,
    madrid_multas,
    murcia,
    navarra,
    osm,
    osm_notes,
    salamanca,
    sct,
)
from .base import Context, Source

log = logging.getLogger(__name__)

REGISTRY: dict[str, Source] = {
    s.key: s
    for s in (
        dgt_freshness.watch(dgt.SOURCE),
        osm.SOURCE,
        osm_notes.SOURCE,
        murcia.SOURCE,
        sct.SOURCE,
        sct.TRAILER,
        dgt_invive.SOURCE,
        euskadi.SOURCE,
        navarra.SOURCE,
        donostia.SOURCE,
        donostia_movil.SOURCE,
        madrid.SOURCE,
        salamanca.SOURCE,
        leon.SOURCE,
        barcelona_multas.SOURCE,
        madrid_multas.SOURCE,
    )
}


@dataclass
class Outcome:
    """What one source gave this run, and how fresh it is."""

    key: str
    result: SourceResult
    up: bool  # this run's fetch worked
    fetched_at: float | None  # when the data in use was fetched; None: never fetched
    error: str = ""


def selected(keys: list[str] | None, provinces: frozenset[str] | None) -> list[Source]:
    """The sources to run: ``keys`` (None: every registered one marked ``default``;
    the key "default" stands for those too), minus those that cover none of the
    selected provinces. An unknown key raises."""
    if keys is None or "default" in keys:
        keys = [k for k in keys or () if k != "default"] + [
            k for k, s in REGISTRY.items() if s.default
        ]
    unknown = sorted(set(keys) - REGISTRY.keys())
    if unknown:
        raise ValueError(f"unknown source(s) {', '.join(unknown)}; known: {', '.join(REGISTRY)}")
    out = []
    for key, source in REGISTRY.items():
        if key not in keys:
            continue
        if provinces is not None and source.provinces is not None:
            if not source.provinces & provinces:
                log.info("source %s covers no selected province; skipped", key)
                continue
        out.append(source)
    return out


def fingerprint(source: Source, ctx: Context) -> str:
    """The settings a result depends on. A last good result fetched with other
    settings (another province, another radius) is not reused."""
    provinces = ",".join(sorted(ctx.provinces)) if ctx.provinces is not None else "all"
    text = repr((source.key, provinces, ctx.boxes, ctx.radius, ctx.stretch_zones))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def run(source: Source, ctx: Context, now: float | None = None) -> Outcome:
    """Fetch one source. On any failure, its last good result for the same
    settings (its weekly lists only if they are this week's); with none, an
    empty result. Never raises."""
    now = time.time() if now is None else now
    ctx = replace(ctx, max_age_s=source.max_age_s)
    key = fingerprint(source, ctx)
    try:
        with net.fail_fast(ctx.spanish_ip_timeout_s, source.spanish_ip_hosts):
            result = source.fetch(ctx)
    except Exception as exc:  # a source is never worth a failed run
        last = store.load_result(source.key, key)
        if last is None:
            log.exception("source %s failed and never succeeded; it adds nothing", source.key)
            return Outcome(source.key, SourceResult(), up=False, fetched_at=None, error=str(exc))
        result, saved = last
        # A list of another week is not this week's list: report this week's as
        # not found, so its metrics and alert say so.
        monday = date.fromordinal(ctx.day.toordinal() - ctx.day.weekday())
        lists = [w if w.week == monday else WeeklyList(w.source, monday) for w in result.lists]
        result = replace(result, lists=lists)
        log.warning(
            "source %s failed (%s); using its last good result from %.1f h ago",
            source.key,
            exc,
            (now - saved) / 3600,
        )
        return Outcome(source.key, result, up=False, fetched_at=saved, error=str(exc))
    store.save_result(source.key, key, result, now=now)
    return Outcome(source.key, result, up=True, fetched_at=now)


def run_all(sources: list[Source], ctx: Context) -> list[Outcome]:
    return [run(s, ctx) for s in sources]


__all__ = ["REGISTRY", "Context", "Outcome", "Source", "run", "run_all", "selected"]
