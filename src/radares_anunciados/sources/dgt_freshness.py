"""How old the DGT fixed-radar file is.

NAP says the file is updated hourly, but on 2026-10-01 it was still the file of
18 Dec 2025, while DGT's own PDF of 3 Aug 2026 lists more fixed radars. The data
in use can only be as fresh as the file, so each DGT fetch also reads the date of
the file it downloads (``dgt.download``, the same cached copy the fetch then
parses) and logs its age, with a warning once it is older than ``STALE_AFTER_S``.
The date is the file's own <publicationTime>; the Last-Modified header only when
the file has none (NAP sends no such header). ``age_s`` is the value for
``/metrics``.

The PDF is not read: it has no coordinates and no open licence.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from email.utils import parsedate_to_datetime

from ..model import SourceResult
from . import dgt
from .base import Context, Source

log = logging.getLogger(__name__)

STALE_AFTER_S = 30 * 86_400  # a file NAP calls hourly, unchanged for a month

_lock = threading.Lock()
_last_modified: float | None = None  # when the file was published, epoch seconds


def parse_last_modified(value: str | None) -> float | None:
    """Epoch seconds of an HTTP date ('Thu, 18 Dec 2025 10:56:20 GMT'), or None."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None


def published(xml: bytes, modified: str | None) -> float | None:
    """Epoch seconds of the file's <publicationTime>, else of ``modified`` (its
    Last-Modified header), else None."""
    when = dgt.publication_time(xml)
    if when is not None:
        return when.timestamp()
    return parse_last_modified(modified)


def check(
    now: float | None = None,
    get: Callable[[], tuple[bytes, str | None]] | None = None,
) -> float | None:
    """Read when the DGT file was published, remember it and log its age. Returns
    the age in seconds. ``get`` returns (file, Last-Modified), ``dgt.get`` by
    default. Never raises: a failed download, or a file with no date, keeps the
    last known date (None before any)."""
    global _last_modified
    now = time.time() if now is None else now
    try:
        xml, header = (get or dgt.get)()
    except Exception as exc:
        log.warning("could not read the DGT fixed-radar file to date it: %s", exc)
        return age_s(now)
    modified = published(xml, header)
    if modified is None:
        log.warning(
            "the DGT fixed-radar file has no publicationTime and no Last-Modified;"
            " its age is unknown"
        )
        return age_s(now)
    with _lock:
        _last_modified = modified
    age = max(0.0, now - modified)
    days = age / 86_400
    day = time.strftime("%Y-%m-%d", time.gmtime(modified))
    if age > STALE_AFTER_S:
        log.warning("DGT fixed-radar file last changed %s, %.0f days ago: stale", day, days)
    else:
        log.info("DGT fixed-radar file last changed %s, %.0f days ago", day, days)
    return age


def last_modified() -> float | None:
    """The file's Last-Modified (epoch seconds) from the latest check that got one."""
    with _lock:
        return _last_modified


def age_s(now: float | None = None) -> float | None:
    """Seconds since the file last changed, by the latest check; None before any."""
    modified = last_modified()
    if modified is None:
        return None
    return max(0.0, (time.time() if now is None else now) - modified)


def watch(source: Source) -> Source:
    """``source`` with a freshness check before each fetch. The file is downloaded
    once: the check dates that copy, and the fetch then reads it from the cache.
    Dating it never fails the fetch. A failed download fails it at once, without
    asking the same hosts a second time from ``source.fetch``."""

    def fetch(ctx: Context) -> SourceResult:
        xml, header = dgt.get(ctx.max_age_s)
        check(get=lambda: (xml, header))
        return source.fetch(ctx)

    return replace(source, fetch=fetch)
