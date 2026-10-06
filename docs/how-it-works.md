# How it works

Every hour the service collects the published radars, turns each into a circle, and makes the radar
zones in Home Assistant match that list. The Home Assistant companion app (iOS or Android) reports when
the phone enters a zone, and the [blueprint](../blueprints/radar_zone_alert.yaml) sends the alert.

## Sources

Sixteen sources: the DGT, the Servei Català de Trànsit, the Basque and Navarra governments, the cities
of Madrid, Salamanca, Donostia, Murcia and León, the traffic fines of Barcelona and Madrid, and
OpenStreetMap's cameras and notes. [Sources](sources.md) lists what each gives, its licence, how often
it changes and which need a Spanish IP.

`RADARES_PROVINCES` picks the area by INE province code (`30` is Murcia, the default), or `all`. A
radar whose source knows its province is kept only in a selected one, and a city's list is fetched
only when its province is selected. OpenStreetMap is searched by a bounding box per province, never
by an Overpass area lookup, which answers 504 under load. `all` is the 52 boxes of the 50 provinces,
Ceuta and Melilla, so it reaches the Balearics and the Canaries too.

A DGT average-speed section is a zone at each end, and the feed also carries it as a line from one end
to the other. Lines never become zones: a stretch tens of kilometres long is no place for a circle. The
one exception is opt-in: with `RADARES_STRETCH_ZONES=on` and a province list, DGT's mobile-radar
stretches get circles along the road.

An OpenStreetMap camera within 150 m of a radar an authority publishes (the DGT, the Servei Català de
Trànsit, a city) is the same camera mapped twice, so it's dropped. An OpenStreetMap average-speed
section goes whole, line and both ends, when one end is within 1 km of an end of a published section
(the two rarely put an end at the same spot) or within 150 m of a published camera.

An open OpenStreetMap note that reports a camera is a `reported` point: unconfirmed, on the map in its
own colour, never a zone. It is left out of the merge altogether: it drops nothing and nothing drops it.
Two radars at the same spot become one zone. The DGT lists both directions of a section with the same
two ends, and the phone has no slots to waste.

Barcelona and Madrid publish every traffic fine as open data, months late. A speed fine names its
place. A place that fines in sessions of a few hours, on scattered days, is where a mobile radar stands;
one that fines day after day at all hours is a fixed camera and gets no zone from the fines. Madrid
writes a mobile radar's place as a street and a number, placed at that address in the city's street
register. A camera that another source publishes or maps within 150 m of such a place already warns
there, so the place gives no zone. The zone is named after the place alone; how often it fined and in
which period are in its attribution, so a new quarter or month does not recreate it.

Murcia's police post their weekly list on X as an image. The press prints it as text: one street and district per
line, such as `Cno. Tiñosa, RM-F6, Los Dolores`. The first part is the street and the last the district.

## From a street name to circles

Murcia has a dozen streets called Avenida Juan Carlos I, so a name alone is not enough.

1. Overpass finds each district, as a place node or a district boundary. "Santiago Zaraiche" in the
   press matches "Santiago y Zaraiche" in OpenStreetMap.
2. Overpass finds the ways with the street's name within 5 km of that district. Names are compared
   without accents, articles or "Don": "Avenida Juan de Borbón" matches "Avenida Don Juan de Borbón".
   When nothing of the stated type is near, another type is accepted. The press wrote "Calle
   Campillo" for what OpenStreetMap maps as "Carril Campillo".
3. Only the stretch near the district is kept, then covered with circles that overlap, so a car
   anywhere on it is inside one. The circles are sized by the street's limit in OpenStreetMap, the
   one on most of the stretch (see below).

A street or district it can't find is skipped and logged, never guessed. The "Radares actualizados"
notification names it and [`/metrics`](alerting.md) exports it, so you know that street has no
warning. Over five real weeks (26 streets) one was skipped: "Carril Molino Batán", which
OpenStreetMap only has as "Camino del Batán".

## How big a zone is

A real iPhone reports that it entered a region about 200 m past the edge, and some 20 s later. A zone
has to reach that far ahead of the radar, or the alert comes after the car has passed it. With the
default `auto`:

| Radar | Radius | At 120 km/h | At 50 km/h |
|---|---|---|---|
| fixed radar, end of a section | 200 m + 40 s at the limit | 1,533 m | 756 m |
| circle along an announced street | 200 m + 20 s at the limit | 867 m | 478 m |

A street is many circles in a row, so each one needs less lead. When the source gives no limit, the
road decides: 120 km/h on a motorway (`A-`, `AP-`), 90 on any other road, 50 on an announced street.
When the limit is known it goes in the zone name, which is the alert's title: "Radar fijo A-7 km 580.3
(límite 100)". A number in `RADARES_FIXED_RADIUS` or `RADARES_STREET_RADIUS` replaces `auto` with a
fixed radius. No zone is ever under 100 m, which would cost the app three of its 20 regions.

## How many zones a phone watches

iOS lets one app watch at most 20 regions. The companion app loads every Home Assistant zone, keeps the
20 nearest to its last position, and picks again after every location event
([`ZoneManagerRegionFilter.swift`](https://github.com/home-assistant/iOS/blob/main/Sources/App/ZoneManager/ZoneManagerRegionFilter.swift)).
So the service loads the radars and the phone chooses.

That choice gets slower with more zones: one pass of the app's filter took 21 ms at 1,000 zones and
316 ms at 5,000 on an Apple-silicon core, and it runs on every location event. So the service loads at most
`RADARES_MAX_ZONES` zones, 1,000 by default. With more radars than that it keeps, in this order: the
streets of a list in force this week, the fixed and section radars nearest to Home Assistant's home,
then the places where traffic fines show a mobile radar stood, then the circles along mobile-radar
stretches, each nearest to home first, then dormant streets, the most recently announced first. A fines
place is one zone where a radar did stand; a stretch takes many circles for kilometres where one may. The rest get no zone. It logs how many and
exports `radares_zones_left_out`.

Android lets one app watch at most 100 geofences, and the companion app does not pick the nearest: it
takes Home Assistant's zones sorted by entity id and stops at 100. A radar outside those 100 fires no
`android.zone_entered`. The blueprint does not depend on that event: Home Assistant itself works out
which zones each location update falls in, so with high accuracy mode on (a location every 5 seconds)
a radar zone alerts whatever the count, as long as one of those locations falls inside it. At 120 km/h
the car covers about 170 m between two locations, and the smallest zone is 200 m across.

## Zones that don't change presence

Every radar zone is *passive*. Home Assistant never sets a person's state to a passive zone, so
`home` / `not_home` automations behave as before. Passive zones still count everywhere the alert needs
them: both apps watch them and fire `ios.zone_entered` or `android.zone_entered`, and each phone's
location tracker lists them in its `in_zones` attribute.

## How the alert fires

The blueprint listens to three things for each phone you pick:

- the phone's location tracker, whose `in_zones` gains the radar zone;
- `ios.zone_entered`, which the iPhone sends about 0.25 s before the tracker update;
- `android.zone_entered`, sent from the same GPS fix as the tracker update, in either order.

The tracker covers the cases where the app events are lost; it alerts for every zone that one of the
phone's reported locations falls in. The app events are lost in cases we measured: iOS drops the
event when it relaunches a terminated app for the region, and fires none for a zone that joins its 20
while the phone is already inside; Android fires none for a zone outside its 100. In all of those the
tracker still lists the zone. The events stay as triggers because, when they do arrive, they can be
first.

Only zones with the icon `mdi:camera-timer` alert. A zone the service keeps but marks as not in force
(`mdi:camera-off`) never alerts. If it comes into force while the phone is inside it, the phone is
alerted at its next location update.

Each phone is alerted through its own notify action, `notify.mobile_app_<device name>`, taken from the
device registry. The mobile app integration keys these actions by device name. Two phones with the
same name, or with names that differ only in case or punctuation, share one action, and only one of
them gets the alerts of both. The notification depends on the phone:

| | iPhone | Android |
|---|---|---|
| Normal | sound, interruption level `time-sensitive` (shows through Focus) or `active` | channel `Radares`, importance high, priority high, `ttl: 0`: a heads-up notification delivered at once |
| Critical | iOS critical alert: full volume, also in silent mode and Do Not Disturb | channel `alarm_stream`: the app plays the notification on the alarm stream, at the alarm volume, also in silent mode |

Android has no exact match for an iOS critical alert. The alarm stream is the closest: it rings when
the ringer is off, at whatever the alarm volume is set to.

The service only touches zones whose name starts with "Radar" and whose icon is `mdi:camera-timer`
or `mdi:camera-off`. Any other zone is left alone, whatever its icon or name.

## A street after its week

A street from a weekly list keeps its zones after the week ends, for `RADARES_DORMANT_WEEKS` (26 by
default). They keep the same name and the same circles; only the icon changes, to `mdi:camera-off`.
The blueprint alerts on `mdi:camera-timer` only, so a dormant zone is silent. When the police announce
the street again, its zones get `mdi:camera-timer` back.

The icon is changed in place, on the same zone. Deleting and creating it would give the zone a new id,
and the phone would keep reporting the old one until the app is next opened. Police lists repeat
streets, so most weeks the zones stay and only icons change. The cache folder keeps the circles of every street announced in
the last `RADARES_DORMANT_WEEKS` weeks. `0` turns this off: a street's zones go when its week ends.
Only a real sync writes that history; `radares feed` and `radares sync --dry-run` read it and leave it
as it was, unless `radares feed --save-history` asks (the published feed does, with a cache of its own). A street whose week is still running keeps its alerting icon even in a run where its source
gave nothing.

## How the phone gets new zones

The iOS app stores zones only while it is open on screen, and only when a zone or person changes at
least 15 s after the last change it stored. A sync that creates 50 zones in one burst lands only the
first. So after a change the service does two things:

1. It sends "Radares actualizados" to the phones in `RADARES_NOTIFY`. Tapping it opens the app, which
   loads the whole list.
2. 20 s later it moves one radar zone by 1 cm, a real change Home Assistant announces, so an app that
   is already open stores the whole set. 1 cm is below the 6 decimals the service compares, so the next
   sync sees no difference. Repeated touches move the zone back and forth, never further.

An icon-only change needs nothing on the phone, so it does not notify.

## One alert per street

A street from a police list is a row of overlapping circles named `Radar anunciado …`, and an
average-speed section is a circle at each end named `Radar de tramo …`. The phone enters each circle
in turn, so the blueprint treats each of these names as one radar: it alerts a phone once and then
ignores that name for that phone until the cooldown ends (10 minutes by default).

Every other zone is its own radar, even when its name is shared. OpenStreetMap cameras without a road
and kilometre are all called `Radar (límite 50)` or plain `Radar`, and they are different cameras, so
the blueprint tells them apart by zone, not by name. A source that draws one radar as several circles
must use one of the two prefixes above, or each circle alerts on its own. A different radar, or a
different phone, is alerted at once.

The blueprint remembers without a helper. When it alerts, it creates a scene named after the phone and
the radar, such as `scene.radares_anunciados_<device id>_radar_anunciado_calle_mayor`: the marker. An
entry whose marker exists is skipped. Once a marker is older than the cooldown and its phone has left
that radar, a template trigger deletes it. No run waits: each run checks, marks and notifies, and runs
go one at a time, so an app event and a tracker update for the same entry alert once.

Comparing the tracker's previous and new `in_zones` by name is not enough on its own, which is why the
markers stay:

- The iPhone's event arrives before the tracker update. Without a marker, the event alerts and then the
  tracker update, which gains the zone, alerts again.
- The two ends of a section are kilometres apart, so the phone is in no radar zone between them.
- Above 100 zones the Android app re-registers its geofences on every sensor pass and fires
  `android.zone_entered` again for a zone you are already in.

Two costs come with it:

- Home Assistant forgets these scenes on restart and on a scene reload. `scene.reload`, every save in
  the scene editor and "reload all YAML" all delete every scene made by `scene.create`. On start, and
  on the `scene_reloaded` event that a reload fires, the blueprint marks every radar a phone is inside.
  A restart or a reload mid-street doesn't alert again, and the next radar alerts as usual.
- The blueprint can't tell whether the push reached the phone. The mobile app integration logs a
  failed push (a timeout or an error from the push service) and carries on, so the blueprint counts it
  as sent. If the alert for the first circle of a street is lost, the rest of that street stays quiet
  until the cooldown ends.

## When a source is down

Downloads are cached for each source's cache age. A failed refresh counts as a failed source, even
with an older copy on disk. An Overpass answer that reports an error is never cached, so the next run
asks again. Each source's last good result is kept in the cache folder: when a source
fails, its last good result is used and logged, so its zones stay. A weekly list in it counts only in
its own week; in a later week the list shows as not found. A source that never answered adds nothing. `/metrics` exports, per source, whether it
answered (`radares_source_up`) and how old its data is (`radares_source_data_age_seconds`). A run
fails only when Home Assistant does. Then Home Assistant keeps the previous zones and the next run
tries again.

## The published feed

[`.github/workflows/feed.yml`](../.github/workflows/feed.yml) runs every 6 hours, and on demand. Its
`publish` job runs on a self-hosted runner in Spain, because `euskadi`, `navarra` and the León council
answer only Spanish addresses. It builds the feed for all of Spain (`RADARES_PROVINCES=all`) from every
registered source:

```sh
radares feed --output feed.geojson --status status.json --save-history
```

and publishes `feed.geojson`, `status.json`, the map in [`site/`](../site/) and
[`LICENSE-DATA.md`](../LICENSE-DATA.md) to GitHub Pages. `status.json` has one row per source:

| Field | Meaning |
|---|---|
| `status` | `ok`: the source answered in this run. `stale`: this run failed; the feed holds its last good result. `missing`: never fetched here; it adds nothing |
| `radars`, `stretches`, `reported` | what the source gave (or its last good result); `reported` counts the unconfirmed notes, which are not counted as radars |
| `in_feed` | its features left in the feed after duplicates are dropped |
| `data_time` | when the data in use was fetched, UTC; `null` for a missing source |
| `updated` | the date the source gives for its last update (for DGT and SCT, the `Last-Modified` the file in use came with, kept with the cached copy; Madrid and Salamanca catalogue dates); `null` when it gives none |
| `error` | why this run's fetch failed |
| `attribution`, `licence`, `spanish_ip` | the source's terms, and whether it answers only Spanish addresses |

Each source's last good result and the announced streets are kept between runs with `actions/cache`,
so a source that is down falls back to its last good copy as described above, and an announced street
turns dormant on the map after its week. The downloads are not kept. A download younger than its
source's cache age is reused without asking the source, so a kept one would report a source as `ok`,
with this run's time, while its site is down. The self-hosted runner is not wiped between runs, so the
job deletes the cache folder before restoring the kept part. With no download kept, every published run
asks every source. A run whose feed has no features at all fails instead of publishing it. The `deploy`
job, on a GitHub-hosted runner, publishes what `publish` built.

A pull request that touches the code, the page or the workflow runs the `check` job instead, on a
GitHub-hosted runner. The two jobs are split by event (`schedule` and `workflow_dispatch`, which no
fork can trigger, against `pull_request`), which keeps ordinary pull requests off the self-hosted
runner. It cannot stop a hostile one: a pull request runs the workflow files of its own merge commit,
so one that edits them could target the self-hosted runner. What stops that is the repository
setting that requires approval of every outside contributor's run, and reading a fork's changes
under `.github/` before approving it. The runner is also ephemeral, one job per registration, and
has no Docker socket. `check` builds the same files from an empty cache without publishing them, and keeps
them as the `feed-site` artifact. Its log lists every source with its state and counts. That runner is
outside Spain, so the job sets `RADARES_SPANISH_IP_TIMEOUT=20`: a source marked Spanish IP gets one try
of at most 20 s per request to its Spain-only hosts (`spanish_ip_hosts`) instead of three of 90 s.
Its other requests, like Overpass or iLeón, keep their normal timeouts. `euskadi` and `navarra` then
show as `missing`; `leon` still reads iLeón, which answers from anywhere. Both jobs build through
the same steps, in [`.github/actions/build-feed`](../.github/actions/build-feed/action.yml).

The map loads Leaflet from unpkg, pinned to one version with an integrity hash, and OpenStreetMap
tiles. A Content-Security-Policy in the page allows nothing else: no analytics, no external fonts.

If the self-hosted runner is offline, a scheduled `publish` job waits in the queue for up to 24 hours
and then fails. Nothing else raises an alarm: GitHub mails the failure to the repository's owner,
and the map shows its warning once `status.json` is a day old.

Which radars are active, and each source's state, are worked out when the feed is built, not in the
browser. When `status.json` is more than a day old the map shows a warning that the feed has stopped
updating. GitHub turns off scheduled workflows in a public repository after 60 days without activity;
re-enable the workflow under Actions when that happens.

## The legal line

Everything comes from published lists and maps. [RGC art. 18.3](https://www.boe.es/buscar/act.php?id=BOE-A-2003-23514#a18)
bans radar detectors and jammers and excludes "los mecanismos de aviso que informan de la posición de
los sistemas de vigilancia del tráfico". Nothing here senses a radar signal. Data from commercial radar
apps is not used: their terms forbid extracting it, and EU database law protects it.
