# Changelog

## 0.3.0 (2026-10-06)

Warns where Barcelona's and Madrid's mobile radars stand, publishes every Spain-only source, and takes
its Home Assistant name.

**Changed, read before updating**

- The project is now `radares-anunciados-ha`. The image is `drumsergio/radares-anunciados-ha:0.3.0`
  (also `ghcr.io/geiserx/radares-anunciados-ha:0.3.0`). The old image name keeps 0.1.0 and 0.2.0 and
  gets no new tags, so change the image line to update.
- The published feed moved to https://geiserx.github.io/radares-anunciados-ha/. The old Pages address
  answers 404.
- The name `radares-anunciados` now belongs to the phone app, which reads this feed, so links to
  `github.com/GeiserX/radares-anunciados` reach the app, not this repo. A blueprint imported before
  0.3.0 points there and can no longer re-import. Its rules did not change in 0.3.0, so nothing is
  needed now; to get later changes, import it again from
  [Getting started](docs/getting-started.md#3-import-the-alert-blueprint) and let it replace your copy.
- The Python package, the `radares` command, the `RADARES_*` settings, the `radares_` metrics, the
  cache folder and the blueprint file keep their names, so a running install needs only the new image.

**Added**

- Fines-derived mobile radar spots for Barcelona and Madrid (`barcelona_multas`, `madrid_multas`): a
  place where the city's open traffic-fine data shows a camera fining in short sessions on scattered
  days. Each spot is a zone named after the place. A spot within 150 m of a camera from another source
  is dropped, and spots are held back for a run in which a camera source has no result yet. Madrid's
  spots are placed with the city's official street register.
- OpenStreetMap enforcement relations: a camera takes its direction and limit from them when the
  node has none, and an average-speed relation becomes a section with its two ends. A mapped section
  that copies a published one is dropped.
- `osm_notes`, off by default: open OpenStreetMap notes reporting a speed camera in Spain, shown on the
  published map as unconfirmed. They never become zones.
- `LICENSE-DATA.md` gives each source's reuse terms, and the basis under Ley 37/2007 for those that
  publish none.
- `status.json` gives each source's own last update date, `updated`, when the source gives one, and
  counts unconfirmed reports apart from radars.

**Fixed**

- The published feed no longer shows the Basque Country and Navarra as `missing`. Their servers
  answer only Spanish addresses, and the feed is now built on a runner in Spain.

## 0.2.0 (2026-10-02)

Covers all of Spain, and keeps warning when a source fails or a week has no list.

**Changed, read before updating**

- The blueprint has a new required input, **Phones**. An automation made from the 0.1.0 blueprint
  shows as unavailable after you re-import it, until you pick its phones. Steps in
  [Getting started](docs/getting-started.md).
- `RADARES_FIXED_RADIUS` and `RADARES_STREET_RADIUS` default to `auto`: the radius follows the road's
  speed limit, so most zones are larger than before. A number keeps a fixed radius.
- The service no longer refuses to sync above 400 zones. `RADARES_MAX_ZONES` (default 1000) keeps the
  radars that matter most and leaves the rest out.
- Days are counted in Spanish time, whatever the container's time zone.

**Added**

- 13 sources behind one registry: DGT fixed radars and mobile-radar stretches, OpenStreetMap, Catalonia,
  the Basque Country, Navarra, Madrid, Salamanca, Donostia, and the police lists of Murcia and León.
  `RADARES_PROVINCES` and `RADARES_SOURCES` choose among them. See [Sources](docs/sources.md).
- The blueprint alerts Android phones as well as iPhones, and triggers on the phone's location tracker,
  so it catches the entries the iPhone app drops. One alert per phone and radar within a cooldown.
- A street from a police list keeps its zones after its period ends, silent, and alerts again when it
  is announced again. The phone does not have to reload zones every week.
- The speed limit in the alert title, and a limit lookup in OpenStreetMap for radars whose source gives
  none.
- A failed source reuses its last good result and never costs its zones. `/metrics` and `/healthz` on
  `RADARES_METRICS_PORT` report sources that are down, a missing weekly list and streets left without a
  zone, with Prometheus rules in [Alerting](docs/alerting.md).
- A published feed with a map, built every 6 hours, and its data licence in
  [LICENSE-DATA.md](LICENSE-DATA.md).

**Fixed**

- An Overpass error answer is no longer cached as if it were data.
- Tests run on the Python the Docker image ships.

## 0.1.0 (2026-09-30)

First release: DGT fixed radars, OpenStreetMap speed cameras and the weekly mobile-radar list of
Murcia's Policía Local as passive Home Assistant zones, with a blueprint that notifies the phone that
enters one.
