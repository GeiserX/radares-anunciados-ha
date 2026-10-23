"""Small JSON files in the cache directory that outlive a run.

- ``sources/<key>.json``: each source's last good result, used when the source
  fails, so a source that is down keeps its zones.
- ``announced.json``: every street a periodic list announced, with its circles,
  so its zones stay (dormant) after the period ends.

A file that can't be written is logged and the run goes on: the sync matters
more than the memory.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path
from typing import Any

from . import net
from .model import Announced, Radar, SourceResult, Stretch, WeeklyList

log = logging.getLogger(__name__)

VERSION = 1  # bump when the shape changes; an older file is then ignored


def _plain(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return value


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def radar_to_json(r: Radar) -> dict:
    return {k: _plain(v) for k, v in asdict(r).items()}


def radar_from_json(d: dict) -> Radar:
    known = {f.name for f in fields(Radar)}
    d = {k: v for k, v in d.items() if k in known}
    dates = {k: _date(d.get(k)) for k in ("valid_from", "valid_to", "reported")}
    return Radar(**{**d, **dates})


def stretch_to_json(s: Stretch) -> dict:
    return {k: _plain(v) for k, v in asdict(s).items()}


def stretch_from_json(d: dict) -> Stretch:
    known = {f.name for f in fields(Stretch)}
    d = {k: v for k, v in d.items() if k in known}
    line = tuple(tuple(p) for p in d["line"]) if d.get("line") else None
    return Stretch(**{**d, "start": tuple(d["start"]), "end": tuple(d["end"]), "line": line})


def _announced(d: dict) -> Announced:
    return Announced(d["street"], d.get("place"))


def list_to_json(w: WeeklyList) -> dict:
    return {k: _plain(v) for k, v in asdict(w).items()}


def list_from_json(d: dict) -> WeeklyList:
    return WeeklyList(
        source=d["source"],
        week=date.fromisoformat(d["week"]),
        published=_date(d.get("published")),
        streets=[_announced(a) for a in d.get("streets", [])],
        skipped=[_announced(a) for a in d.get("skipped", [])],
        placed=dict(d.get("placed") or {}),
    )


def result_to_json(r: SourceResult) -> dict:
    return {
        "radars": [radar_to_json(x) for x in r.radars],
        "stretches": [stretch_to_json(x) for x in r.stretches],
        "lists": [list_to_json(x) for x in r.lists],
        "updated": r.updated,
    }


def result_from_json(d: dict) -> SourceResult:
    return SourceResult(
        radars=[radar_from_json(x) for x in d.get("radars", [])],
        stretches=[stretch_from_json(x) for x in d.get("stretches", [])],
        lists=[list_from_json(x) for x in d.get("lists", [])],
        updated=d.get("updated"),
    )


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": VERSION, **data}, ensure_ascii=False), "utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)


def _source_path(key: str) -> Path:
    return net.cache_dir() / "sources" / f"{key}.json"


def save_result(key: str, fingerprint: str, result: SourceResult, now: float | None = None) -> None:
    saved = time.time() if now is None else now
    data = {"fingerprint": fingerprint, "saved": saved, "result": result_to_json(result)}
    _write(_source_path(key), data)


def load_result(key: str, fingerprint: str) -> tuple[SourceResult, float] | None:
    """The last good result and when it was fetched. None if there is none, or
    it was fetched for other settings (another province, another radius)."""
    data = _read(_source_path(key))
    if data is None:
        return None
    if data.get("fingerprint") != fingerprint:
        log.info("last good %s result was fetched with other settings; not used", key)
        return None
    try:
        return result_from_json(data["result"]), float(data["saved"])
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("ignoring a damaged last good %s result: %s", key, exc)
        return None


def _announced_path() -> Path:
    return net.cache_dir() / "announced.json"


def load_announced() -> list[Radar]:
    data = _read(_announced_path())
    if data is None:
        return []
    try:
        return [radar_from_json(x) for x in data.get("radars", [])]
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("ignoring a damaged history of announced streets: %s", exc)
        return []


def save_announced(radars: list[Radar]) -> None:
    _write(_announced_path(), {"radars": [radar_to_json(r) for r in radars]})
