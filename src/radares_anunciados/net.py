"""HTTP with a real User-Agent and a couple of retries (Overpass 504s under load)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = "radares-anunciados-ha (+https://github.com/GeiserX/radares-anunciados-ha)"

# Set by ``fail_fast``: (seconds, hosts). A request to one of the hosts gets one
# try and at most that many seconds.
_cap: ContextVar[tuple[int, frozenset[str]] | None] = ContextVar("timeout_cap", default=None)


@contextmanager
def fail_fast(seconds: int | None, hosts: frozenset[str]) -> Iterator[None]:
    """Within the block, a request to ``hosts`` (a domain and its subdomains) gets
    one try and a timeout of at most ``seconds`` (None: no change). A source runs in
    it with the hosts that answer only Spanish addresses, on a runner outside Spain,
    where each request to them would time out three times."""
    token = _cap.set(None if seconds is None else (seconds, hosts))
    try:
        yield
    finally:
        _cap.reset(token)


def _cap_for(url: str) -> int | None:
    cap = _cap.get()
    if cap is None:
        return None
    host = urllib.parse.urlsplit(url).hostname or ""
    if any(host == d or host.endswith("." + d) for d in cap[1]):
        return cap[0]
    return None


def timeout_s(default: int, url: str) -> int:
    """``default``, or the ``fail_fast`` cap for ``url`` when that is lower."""
    cap = _cap_for(url)
    return default if cap is None else min(default, cap)


def get(
    url: str,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 90,
    tries: int = 3,
    sent: dict[str, str] | None = None,
) -> bytes:
    """The response body. ``sent``, when given, receives the response headers,
    names in lower case."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    if _cap_for(url) is not None:
        timeout, tries = timeout_s(timeout, url), 1
    for attempt in range(1, tries + 1):
        request = urllib.request.Request(
            url, data=body, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if sent is not None:
                    sent.update({k.lower(): v for k, v in response.headers.items()})
                return response.read()
        except OSError:
            if attempt == tries:
                raise
            time.sleep(10 * attempt)
    raise AssertionError("unreachable")


def cache_dir() -> Path:
    return Path(os.environ.get("RADARES_CACHE", Path.home() / ".cache" / "radares-anunciados"))


def cached_get(
    url: str,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    max_age_s: int = 86_400,
    validate: Callable[[bytes], None] | None = None,
) -> bytes:
    """``get`` through a file cache. A copy younger than ``max_age_s`` is used as is;
    an older one is refreshed. A failed refresh raises, also with an old copy on
    disk: the source then reuses its last good result and reports itself down
    (``sources.run``), instead of looking like a source that answered.

    ``validate`` raises on a body that is no answer (``overpass_answer``). It runs
    before the write, so an error answer is never kept for the cache lifetime, and
    on a cached copy too: a copy that fails it (kept before the check existed) is
    stale and asked again."""
    return _cached(url, data, headers, max_age_s, validate, dated=False)[0]


def cached_get_dated(url: str, max_age_s: int = 86_400) -> tuple[bytes, str | None]:
    """``cached_get``, and the ``Last-Modified`` the server sent with these very
    bytes (None when it sent none). The header is kept beside the cached copy, so a
    copy reused from the cache keeps the date of its own download: a newer date the
    server gives now would describe a file this run does not use."""
    return _cached(url, None, None, max_age_s, None, dated=True)


def _cached(
    url: str,
    data: dict[str, str] | None,
    headers: dict[str, str] | None,
    max_age_s: int,
    validate: Callable[[bytes], None] | None,
    dated: bool,
) -> tuple[bytes, str | None]:
    path = _cache_path(url, data)
    stamp = path.with_name(path.name + ".last-modified")
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        cached = path.read_bytes()
        try:
            if validate is not None:
                validate(cached)
            return cached, _read_stamp(stamp) if dated else None
        except ValueError as exc:
            log.warning("cached copy of %s fails its check, asking again: %s", url, exc)
    sent: dict[str, str] = {}
    if dated:
        body = get(url, data=data, headers=headers, sent=sent)
    else:
        body = get(url, data=data, headers=headers)
    if validate is not None:
        validate(body)
    modified = sent.get("last-modified")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The old stamp goes first: a copy is never left with another copy's date.
        stamp.unlink(missing_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        if modified:
            stamp.write_text(modified, "utf-8")
    except OSError as exc:  # an unwritable cache must not fail a download that worked
        log.warning("could not cache %s in %s: %s", url, path.parent, exc)
    return body, modified


def last_modified_day(value: str | None) -> str | None:
    """The day of an HTTP date ('Thu, 18 Dec 2025 10:56:20 GMT' -> '2025-12-18'), UTC,
    or None."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(UTC).date().isoformat()
    except (TypeError, ValueError):
        return None


def _read_stamp(stamp: Path) -> str | None:
    try:
        return stamp.read_text("utf-8").strip() or None
    except OSError:
        return None


def _cache_path(url: str, data: dict[str, str] | None) -> Path:
    key = hashlib.sha256(json.dumps([url, data], sort_keys=True).encode()).hexdigest()[:32]
    return cache_dir() / key


def cached_copy(
    url: str,
    data: dict[str, str] | None = None,
    validate: Callable[[bytes], None] | None = None,
) -> bytes | None:
    """The copy ``cached_get`` keeps for this request, however old; None when there
    is none or it fails ``validate``. Only for data that adds to a source's own
    answer (the OSM relations around its cameras): a source's own data comes from
    ``cached_get``, so a failed refresh still shows the source as down."""
    try:
        body = _cache_path(url, data).read_bytes()
        if validate is not None:
            validate(body)
    except (OSError, ValueError):
        return None
    return body


def overpass_answer(body: bytes) -> None:
    """Raise ValueError unless ``body`` is a complete Overpass answer. A query that
    ran out of time or memory still answers 200, with a ``remark`` and whatever it
    had found so far (often nothing); a proxy in front of it may answer HTML."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"Overpass answered with no JSON: {body[:80]!r}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise ValueError(f"Overpass answered with no elements: {body[:80]!r}")
    if data.get("remark"):
        raise ValueError(f"Overpass remark: {data['remark']}")
