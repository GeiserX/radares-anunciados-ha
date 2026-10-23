import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radares_anunciados import net, sources
from radares_anunciados.model import SourceResult
from radares_anunciados.sources import Context, Source, dgt, dgt_freshness
from radares_anunciados.speed import Radius

FIX = Path(__file__).parent / "fixtures"
XML = (FIX / "dgt_radares.xml").read_bytes()
# The same file with no <publicationTime>: only a header can date it.
UNDATED = XML.replace(
    b"<_0:publicationTime>2025-12-18T09:56:52.406+01:00</_0:publicationTime>", b""
)
# The real HEAD answer for the fixed-radar file on 2026-10-01 (infocar).
HEADERS = dict(
    line.split(": ", 1)
    for line in (FIX / "dgt_radares_head.txt").read_text().splitlines()
    if ": " in line
)
CHANGED = datetime(2025, 12, 18, 10, 56, 20, tzinfo=UTC).timestamp()  # Last-Modified
PUBLISHED = datetime(2025, 12, 18, 8, 56, 52, 406_000, tzinfo=UTC).timestamp()  # in the file
ASKED = datetime(2026, 10, 1, 17, 42, 50, tzinfo=UTC).timestamp()


@pytest.fixture(autouse=True)
def forget(monkeypatch):
    monkeypatch.setattr(dgt_freshness, "_last_modified", None)


def ctx() -> Context:
    return Context(day=None, provinces=None, boxes=(), radius=Radius())


def test_the_fixture_has_a_publication_time_and_the_undated_copy_none():
    assert UNDATED != XML
    assert dgt.publication_time(XML).timestamp() == PUBLISHED
    assert dgt.publication_time(UNDATED) is None


def test_reads_the_files_last_modified():
    assert dgt_freshness.parse_last_modified(HEADERS["last-modified"]) == CHANGED
    assert dgt_freshness.parse_last_modified(None) is None
    assert dgt_freshness.parse_last_modified("not a date") is None


def test_the_age_comes_from_the_files_publication_time(caplog):
    with caplog.at_level(logging.INFO):
        age = dgt_freshness.check(now=ASKED, get=lambda: (XML, None))
    assert age == ASKED - PUBLISHED
    assert round(age / 86_400) == 287
    assert "2025-12-18, 287 days ago: stale" in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert dgt_freshness.last_modified() == PUBLISHED
    assert dgt_freshness.age_s(now=ASKED + 3600) == ASKED - PUBLISHED + 3600


def test_publication_time_wins_over_the_header():
    newer = "Fri, 02 Oct 2026 07:00:00 GMT"
    assert dgt_freshness.check(now=ASKED, get=lambda: (XML, newer)) == ASKED - PUBLISHED


def test_a_file_without_publication_time_is_dated_by_its_header():
    age = dgt_freshness.check(now=ASKED, get=lambda: (UNDATED, HEADERS["last-modified"]))
    assert age == ASKED - CHANGED


def test_a_file_400_days_old_is_stale_and_a_fresh_one_is_not(caplog):
    old = XML.replace(b"2025-12-18T09:56:52", b"2025-08-27T09:56:52")
    with caplog.at_level(logging.INFO):
        age = dgt_freshness.check(now=ASKED + 0, get=lambda: (old, None))
    assert round(age / 86_400) == 400
    assert "stale" in caplog.text
    caplog.clear()
    fresh = XML.replace(b"2025-12-18T09:56:52", b"2026-10-01T09:56:52")
    with caplog.at_level(logging.INFO):
        dgt_freshness.check(now=ASKED, get=lambda: (fresh, None))
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "stale" not in caplog.text


def test_a_failed_check_keeps_the_last_known_date_and_never_raises(caplog):
    dgt_freshness.check(now=ASKED, get=lambda: (XML, None))

    def down():
        raise OSError("connection refused")

    assert dgt_freshness.check(now=ASKED + 60, get=down) == ASKED + 60 - PUBLISHED
    assert dgt_freshness.check(now=ASKED + 60, get=lambda: (UNDATED, None)) == (
        ASKED + 60 - PUBLISHED
    )
    assert "connection refused" in caplog.text


def test_nothing_known_before_any_check():
    assert dgt_freshness.age_s() is None
    assert dgt_freshness.check(get=lambda: (UNDATED, None)) is None


def test_watch_checks_before_each_fetch_and_dating_never_fails_it(monkeypatch):
    asked = []
    monkeypatch.setattr(dgt, "get", lambda max_age_s=86_400: asked.append(max_age_s) or (XML, None))
    result = SourceResult()
    source = dgt_freshness.watch(Source("dgt", lambda ctx: result, "a", "l"))
    assert source.fetch(ctx()) is result
    assert asked == [86_400]
    assert dgt_freshness.last_modified() == PUBLISHED
    # a file the check cannot date still gets fetched
    monkeypatch.setattr(dgt, "get", lambda max_age_s=86_400: (UNDATED, None))
    assert source.fetch(ctx()) is result


def test_a_dgt_outage_asks_each_host_once(monkeypatch):
    """Both hosts down: the check and the fetch share one download, so the whole
    fetch asks each host once, not once for the check and again for the fetch."""
    asked = _hosts(monkeypatch, {u: OSError("HTTP Error 502") for u in dgt.URLS})
    with pytest.raises(OSError, match="no host answered"):
        sources.REGISTRY["dgt"].fetch(ctx())
    assert asked == list(dgt.URLS)


def test_a_time_without_offset_is_read_in_spain_for_the_day_and_the_age():
    naive = XML.replace(b"2025-12-18T09:56:52.406+01:00", b"2025-12-18T09:56:52.406")
    assert naive != XML
    assert dgt.published_day(naive, None) == "2025-12-18"
    assert dgt_freshness.published(naive, None) == PUBLISHED  # Madrid is UTC+1 in December
    assert dgt_freshness.check(now=ASKED, get=lambda: (naive, None)) == ASKED - PUBLISHED


# --- the two hosts -------------------------------------------------------------


def _hosts(monkeypatch, answers):
    """``net.cached_get_dated`` answering each DGT host from ``answers``
    (bytes, or an exception to raise), and the list of hosts asked."""
    asked = []

    def get(url, max_age_s=86_400):
        asked.append(url)
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer, None

    monkeypatch.setattr(net, "cached_get_dated", get)
    return asked


def test_nap_is_read_first_and_infocar_never_asked_when_it_answers(monkeypatch):
    asked = _hosts(monkeypatch, {dgt.URLS[0]: XML, dgt.URLS[1]: OSError("not to be asked")})
    result = sources.REGISTRY["dgt"].fetch(ctx())
    assert result.radars
    assert asked == [dgt.URLS[0], dgt.URLS[0]]  # the freshness check, then the fetch
    assert "nap.dgt.es" in dgt.URL and dgt.URL == dgt.URLS[0]
    assert {r.url for r in result.radars} == {dgt.URLS[0]}


def test_infocar_is_read_when_nap_fails(monkeypatch):
    asked = _hosts(monkeypatch, {dgt.URLS[0]: OSError("HTTP Error 502"), dgt.URLS[1]: XML})
    xml, day = dgt.download()
    assert xml == XML and day == "2025-12-18"
    assert asked == list(dgt.URLS)
    assert dgt.parse(xml, None)


def test_download_raises_when_every_host_fails(monkeypatch):
    _hosts(monkeypatch, {u: OSError("HTTP Error 502") for u in dgt.URLS})
    with pytest.raises(OSError, match="no host answered"):
        dgt.download()


# --- the date of the bytes in use ------------------------------------------------


def test_an_answer_without_last_modified_is_dated_by_publication_time(monkeypatch, tmp_path):
    """NAP sends no Last-Modified: the day must come from the file itself."""
    monkeypatch.setenv("RADARES_CACHE", str(tmp_path))
    monkeypatch.setattr(net, "get", lambda url, **kw: XML)
    result = sources.REGISTRY["dgt"].fetch(ctx())
    assert result.updated == "2025-12-18"
    assert all(r.attribution.endswith(", actualizado 2025-12-18") for r in result.radars)


def test_an_answer_with_a_header_and_no_publication_time_uses_the_header(monkeypatch, tmp_path):
    """The file is downloaded once a day. A copy cached yesterday keeps yesterday's
    Last-Modified: the date must describe the radars in the feed, not a newer file."""
    monkeypatch.setenv("RADARES_CACHE", str(tmp_path))
    downloads = []

    def get(url, data=None, headers=None, sent=None, **kw):
        downloads.append(url)
        if sent is not None:
            sent["last-modified"] = "Thu, 01 Oct 2026 10:00:00 GMT"
        return UNDATED

    monkeypatch.setattr(net, "get", get)
    source = sources.REGISTRY["dgt"]
    first = source.fetch(ctx())
    assert first.updated == "2026-10-01" and first.radars
    assert all(r.attribution.endswith(", actualizado 2026-10-01") for r in first.radars)
    second = source.fetch(ctx())
    assert downloads == [dgt.URL]  # the cached copy was used, header and all
    assert second.updated == "2026-10-01"
    # a download with neither gives no date, and no older one is kept
    (tmp_path / next(p.name for p in tmp_path.iterdir() if "." not in p.name)).unlink()
    monkeypatch.setattr(net, "get", lambda url, **kw: UNDATED)
    assert source.fetch(ctx()).updated is None
    assert source.fetch(ctx()).updated is None
