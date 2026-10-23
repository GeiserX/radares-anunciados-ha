import pytest

from radares_anunciados import osm_limits


@pytest.fixture(autouse=True)
def no_speed_limit_queries(monkeypatch):
    """Every collect runs the OSM speed-limit lookup (``speed.LOOKUPS``). In a
    test it finds no network and leaves the limits unknown, as a failed query
    does; tests of the lookup give it fixtures instead."""

    def offline(points):
        raise OSError("tests never touch the network")

    monkeypatch.setattr(osm_limits, "_ask", offline)


@pytest.fixture(autouse=True)
def no_geocoder_requests(monkeypatch, tmp_path):
    """A street the map search misses is asked of CartoCiudad and Nominatim
    (``geocode``). In a test it finds no network and stays unplaced, as when the
    geocoders are down; tests of the geocoders give them fixtures instead."""
    from radares_anunciados import geocode

    def offline(self, provider, url, params):
        raise OSError("tests never touch the network")

    monkeypatch.setattr(geocode.Geocoder, "_get", offline)
    # the answers go to the test's own cache folder, never ~/.cache; a test that
    # sets RADARES_CACHE itself moves them there, through the real Geocoder._path
    monkeypatch.setenv("RADARES_CACHE", str(tmp_path / "cache"))
