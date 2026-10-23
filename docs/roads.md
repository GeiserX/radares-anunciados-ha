# roads.json

`feed.geojson` gives one point per radar and, for a stretch, its two published ends joined by a straight
line. A phone that measures everything as the crow flies warns on a parallel road, on an overpass and
on the opposite carriageway, and misses a stretch whose road bends away from the straight line.

`roads.json` is a second file, next to the feed, that adds the road itself:

- for every stretch, the road between its two published ends, and the last 1.5 km of road that leads
  into each end (the gates);
- for every fixed, section and trailer radar, the last 1.5 km of road that leads to it, in the direction
  of the traffic it watches.

The feed also gains a travel bearing on the radars whose direction the publisher's data and the map
agree on. Everything is built once, when the published feed is built, from the OpenStreetMap extract
of Spain. Nothing here runs inside a Home Assistant container.

URL: https://geiserx.github.io/radares-anunciados-ha/roads.json, listed in `status.json`. The feed's own
properties are in [feed.md](feed.md).

## The file

```json
{
 "attribution": "© OpenStreetMap contributors (ODbL 1.0)",
 "features": {
  "<point id>": {"approaches": [{"bearing": 123, "polyline": "<polyline>"}]},
  "<stretch id>": {
   "line": "<polyline>",
   "gates": {"start": [{"bearing": 41, "polyline": "..."}], "end": [{"bearing": 220, "polyline": "..."}]}
  }
 },
 "precision": 5,
 "version": 1
}
```

- Keys are sorted and there is no timestamp inside, so the file changes, and its ETag with it, only
  when a road or the set of radars changes. It is written without whitespace.
- `features` is keyed by the feature `id` of `feed.geojson`. A radar or stretch with no entry has no
  road found: a consumer uses the feed's geometry, as before this file existed.
- `bearing` is a whole number of degrees from north (0 to 359): the direction a car drives at the
  approach's last vertex.

### Polylines

Google's encoded polyline format, precision 5 (1e-5 degrees, about 1.1 m), latitude then longitude per
vertex: https://developers.google.com/maps/documentation/utilities/polylinealgorithm. Reference vector:
`_p~iF~ps|U_ulLnnqC_mqNvxq`@` decodes to `(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)`.

Every polyline is simplified with Douglas-Peucker before it is encoded: 10 m for a stretch line, 5 m
for an approach. A dropped vertex is never further than that from the line that is kept.

### Stretch lines

`line` runs from the feed's first vertex of the stretch to its last. Its first and last vertices are the
published ends (to the precision of the encoding). It has no direction: a car may drive it either way.

The road is found with the routing that `dgt_invive.route` already uses for DGT's mobile-radar
stretches: the ways of the stretch's road number, in the box of its two ends plus 5 km, each way's end
joined to every other way within 500 m (at twice the cost, to cross an untagged roundabout), then the
shortest path. Where `route` joins a published end to a node that lies behind it, the line drops that
node, so it never starts by driving backwards.

A line is published only when it fits what the publisher says:

- with `km_from` and `km_to`: the road is 0.85 to 1.15 times the km range. On the live feed the ratio
  of the lines kept has a median of 1.00, p10 0.97 and p90 1.01. The feed's own check
  (`dgt_invive.plausible`, 0.5 times the range less 1 km to 1.5 times plus 2 km) is far wider. It lets
  a 0.12 and a 1.39 through, and a phone that measures distance along a wrong line warns wrong.
- without km (OpenStreetMap sections): both ends snap to ways of the same highway class (a `_link`
  counts as its road), the path uses only that class, and it is no longer than 1.6 times the straight
  line plus 500 m.
- a stretch whose ends are under 100 m apart gets nothing.

### Approaches and gates

An approach is the road a car drives to reach a place, up to 1,500 m of it. It runs in the direction of
travel and its last vertex is the place:

- for a radar, the radar's published point moved onto the road (at most 40 m);
- for a gate, the stretch's published end.

The road is walked backwards from that place. Where several roads arrive at a junction, the walk takes
the one with the same road number, then the same highway class, then the smallest turn. It never takes a
turn sharper than 100 degrees, and stops after 1,500 m, at a dead end or at a junction it already passed.
An approach shorter than 200 m is left out: it says nothing the straight line does not.

A radar gets one approach per direction it watches:

- one, when the feed gives it a `bearing`, on the carriageway that runs that way (the nearest way of the
  same road within 60 m that runs within 45 degrees of it, when the nearest way runs the other way);
- one per carriageway when the source says `both` directions;
- otherwise, one per direction a car may drive the road it stands on: one on a one-way carriageway, two
  on a two-way road.

A stretch gets one gate per end: the carriageway that runs into the stretch there, in line with the
stretch's first (or last) 50 m. The list form leaves room for a second.

A `section` point whose coordinates are, bit for bit, the first or last vertex of a stretch gets no
entry. The app folds those cameras into their stretch and never looks their id up; on 2026-10-23 that
is 332 of the 363 section points.

## Direction

Each radar snapped to a road can get up to three signals. Each names one of the two ways along that road.

| Signal | `bearing_source` | Where it comes from |
|---|---|---|
| S1 official km | `official_km` | `km_direction` (DGT `directionRelative`, Navarra creciente/decreciente) and `km` |
| S2 carriageway | `oneway` | the radar stands on a one-way carriageway and the other carriageway of the same road runs the other way within 60 m, at least 5 m further away |
| S3 OSM tag | `osm_tag` | the OpenStreetMap `direction` as a bearing within 30 degrees of a way along the road, or `forward` / `backward` |

**S1.** The publisher says the radar watches traffic driving up (`positive`) or down (`negative`) the km
markers. To turn that into a way along the road, the step walks the radar's road both ways for 25 km
and looks for km anchors: other radars of the feed with the same road number and a km, the ends of
stretches on that road with `km_from` / `km_to`, and OpenStreetMap `highway=milestone` nodes (within
30 m of a way of the road). The way in which the km grow is the positive one.

An anchor counts only when the road walked to reach it matches its km difference: within 15 % of the
difference plus 300 m, and at least 0.5 km of markers away. A road number can carry two km sequences
(a renumbered stretch, a variant): in DGT's own file, the A-2 markers 122.0 and 136.3 lie 22.1 km apart
along the road, and GR-30 16.2 and 17.6 lie 13.3 km apart. An anchor from the other sequence would flip
the answer; the walked distance gives it away. With one valid anchor its answer is taken; when two
valid anchors disagree, S1 says nothing.

**Decision.** Every signal present is compared:

- any two that oppose: no bearing (the id and the signals are in the audit file);
- S1 and S2 oppose: the radar watches the other carriageway. S1 wins, the approach goes on the
  carriageway that runs S1's way, and the confidence is low;
- an OSM tag against a one-way road: no bearing;
- otherwise the strongest signal gives the bearing (S1, then S2, then S3), and
  `direction_confidence` is `high` only when S1 and S2 are both present and agree.

Only S1 with S2 is high, because they are the only two independent signals where one is official: the
publisher's own direction, checked against the shape of the road the radar stands on. S2 or S3 alone is
the map's word only, and a radar can stand between two carriageways or be tagged the wrong way. The
app hides a radar on the opposite carriageway only when the confidence is high; a low one only makes the
warning silent.

Measured on the live feed of 2026-10-23 (with the `km`, `km_direction` and `road_ref` properties) and
the extract of 2026-10-20:

- S1 resolved 457 of 681 DGT and Navarra radars; no valid anchor or the snapped way not on the
  publisher's road for the rest. Where S1 and S2 were both present, they agreed on 227 of 251 (90 %).
  The 24 others are published with S1's direction, low confidence.
- S3 named a way for 717 OpenStreetMap tags; 36 of them ran against the one-way way or S2 and were
  dropped.
- 1,429 radars got a bearing, 227 of them high.
- Checked against an independent signal, the town DGT writes in the radar's name ("sentido ZARAGOZA"):
  for the 47 high radars whose town is a known city, 46 bearings point toward it (within 90 degrees of
  the straight line to the town).

## Gates on a bad build

`radares roads` exits non-zero and publishes nothing new when:

- roads.json is over 2,000,000 bytes, or over 1,200,000 bytes gzipped (on 2026-10-23: 810,310 and
  396,626);
- fewer than 60 % of the stretches with a road number were followed;
- fewer than 70 % of the radars got at least one approach.

The ratios count only features inside the extract's header box (`bbox` in `status.json`), so a clip of
one province reports its own coverage rather than a few percent of Spain. On a failure the feed is left
as `radares feed` wrote it, the previous roads.json is put back from the cache, and `status.json` says
`stale` with the error.

## status.json

```json
"roads": {"file": "roads.json", "status": "ok", "generated": "2026-10-23T12:00:00+00:00",
          "osm_date": "2026-10-20", "bbox": [-9.779014, 35.154153, 5.098525, 44.14855],
          "stretches_in_bbox": 1520, "stretches_followed": 1313, "stretches_with_gates": 1269,
          "points_in_bbox": 2309, "points_with_approaches": 2233, "points_with_bearing": 1429,
          "points_high_confidence": 227, "bytes": 810310, "error": ""}
```

`status` is `ok` or `stale` (this run failed; the last good file is published, or none on a first run).
The block is absent when the step never ran. `osm_date` is the extract's replication timestamp, or the
file's date when the header has none. Geofabrik's Spain extract has no Canary Islands, Ceuta or Melilla,
so radars there get no entry.

## How it runs

The `publish` job of `feed.yml` runs on the self-hosted runner:

1. **Refresh the OpenStreetMap extract.** `spain-latest.osm.pbf` from Geofabrik (1.5 GB, rebuilt daily)
   lives at `${{ runner.workspace }}/../radares-pbf/`, which is `_work/radares-pbf` in the runner's
   bind-mounted `_work` folder. The runner wipes `runner.temp` at the start of every job and an image
   update wipes its tool cache, so neither can hold it. The copy is refreshed when it is a week old,
   with `curl --time-cond` and `--remote-time`. A failed download keeps the old copy and never fails
   the job.
2. **Build the feed**, then `radares roads` in the same composite action (`.github/actions/build-feed`).
   pyosmium is in the `roads` dependency group, which only that action installs (`uv sync --no-dev
   --group roads`). The Docker image runs `uv sync --no-dev` and never gets it, and `radares feed` never
   imports it.
3. **Keep the last good roads.json.** The step copies its output and status block to
   `$RADARES_CACHE/roads`, saved with `actions/cache` under its own key (`roads-state-`). A later failed
   run puts that copy back.

The pull request job runs the same step against `tests/fixtures/roads/clip.osm.pbf`, a 2.3 MB extract
of the drivable roads around Murcia (box -1.5, 37.75 to -0.9, 38.2) with its header box set, and fails
when the step does. It never downloads from Geofabrik.

Cost on a Mac mini (M-series, NVMe), the whole feed: about 4 minutes to read the extract and 1.5 to 2.5
minutes for the roads, 3.1 GB peak memory. The runner's disk is slower; the job allows 60 minutes.

### Running it locally

On a Mac mini, never on a laptop with little memory. The extract goes on the external disk:

```sh
scratch=/Volumes/Data/scratch/radares-roads-$(date +%s)
mkdir -p "$scratch" && cd "$scratch"
curl -fL --remote-time -o spain-latest.osm.pbf https://download.geofabrik.de/europe/spain-latest.osm.pbf
curl -fL -o feed.geojson https://geiserx.github.io/radares-anunciados-ha/feed.geojson
echo '{}' > status.json
cd ~/radares-anunciados-ha && uv sync --locked --group roads
/usr/bin/time -l uv run radares roads --feed "$scratch/feed.geojson" --status "$scratch/status.json" \
  --pbf "$scratch/spain-latest.osm.pbf" --output "$scratch/roads.json" --audit "$scratch/audit.csv"
```

`--audit` writes one row per radar: the snap distance, each signal with its anchor and walked
distance, the decision, the bearing and the confidence. Delete the scratch folder afterwards.

## Licence

roads.json is a database derived from OpenStreetMap, under the
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), credited "© OpenStreetMap contributors".
A feed feature with an entry in roads.json or a derived bearing gets `; road: © OpenStreetMap
contributors (ODbL 1.0)` added to its `attribution`, unless it already credits OpenStreetMap.
