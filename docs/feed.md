# feed.geojson

The published feed is one GeoJSON `FeatureCollection` at
https://geiserx.github.io/radares-anunciados-ha/feed.geojson, rebuilt every 6 hours. The iOS app and the
map in `site/` read it; Home Assistant users build their zones from the same sources inside their own
container, not from this file. This page is the contract: what each feature holds and what may change.

## Format

- JSON with no whitespace between tokens (`feed.dumps`: `separators=(",", ":")`, UTF-8, `ensure_ascii`
  off). The app downloads it on a mobile connection; no consumer reads its layout.
- Every feature has a string `id`, stable across runs, a `geometry` and `properties`.
- A change to this file is additive: a new property is optional, an existing property keeps its name and
  type, and the geometry of an existing feature does not change.

## Geometry

- A radar is a `Point`, `[lon, lat]` as the source publishes it (converted to WGS84 where the source uses
  UTM). A Point is never turned into a line.
- A stretch (`kind` `stretch`) is a `LineString`. In the published feed nearly every one has two vertices,
  the two published ends, joined by a straight line (on 2026-10-23 only two Salamanca sections had more).
  A Home Assistant build with stretch zones on follows DGT mobile-radar stretches along the road and
  writes more vertices. No other geometry type is written.
- The two cameras of a section are also Points, with ids `<stretch id>-from` and `<stretch id>-to`. Their
  coordinates are bit for bit the first and last vertex of the stretch's line, so a consumer can match
  them by exact equality.

## Properties

Every feature:

| Property | Type | Meaning |
|---|---|---|
| `name` | string | what the driver reads, in Spanish |
| `source` | string | the source key (`docs/sources.md`) |
| `kind` | string | `fixed`, `section`, `trailer`, `mobile_announced`, `mobile_stretch`, `mobile_recurring`, `reported` (Points); `stretch` (LineStrings) |
| `active` | bool | false: a street from a periodic list whose period ended (kept, silent) |
| `maxspeed` | int or null | km/h, from the source or OpenStreetMap |
| `direction` | string or null | as the source writes it; see below |
| `province` | string or null | INE province code, `"30"` |
| `url` | string or null | where the position was published |
| `attribution` | string | the source's credit and licence, with `, actualizado <day>` when the source dates its data |

Points also have `radius_m` (int), `valid_from`, `valid_to` and `reported` (ISO dates or null).
Stretches also have `road` (string or null), `km_from` and `km_to` (numbers or null).

### `direction`

A free string that keeps the source's own words. Four kinds of value appear:

- `null`: the source says nothing;
- `"both"`: DGT's mobile-radar stretches;
- a compass bearing in degrees as text, `"75"`: OpenStreetMap cameras, from the node's `direction` tag or
  the way of its enforcement relation (on 2026-10-23, 8 nodes carried OSM's own words `"forward"` or
  `"backward"` and one `"10;190"`);
- a place or sense in Spanish: the town the traffic heads to (`"ZARAGOZA"`, DGT, Euskadi, Madrid), or
  `"creciente"` / `"decreciente"` (Navarra).

Nothing here says which value is reliable. A consumer that needs the travel direction reads
`km_direction` or the derived `bearing` below.

### Road, km and monitored direction (Points, optional)

Written only when the source gives them; the key is absent otherwise, never null.

| Property | Type | Sources | Meaning |
|---|---|---|---|
| `road_ref` | string | `dgt`, `navarra`, `sct`, `sct_remolc` | the publisher's road number, `"A-7"`. Named `road_ref`, not `road`, because the app words a notification differently for a Point with `road` |
| `km` | number | `dgt`, `navarra`, `sct`, `sct_remolc` | the km marker of the radar. A Catalan section end has none (its PK is a range) |
| `km_direction` | string | `dgt`, `navarra` | which traffic the radar watches: `"positive"` (km markers growing), `"negative"` (falling), `"both"` |

`dgt` takes them from the DATEX reference point of each fixed radar (`roadNumber`,
`referencePointDistance` / 1000, `directionRelative`); `navarra` from its own code (`C` creciente is
`positive`, `D` decreciente is `negative`); `sct` gives no direction. DGT section ends get none of the
three: the file's two ends of one section sometimes disagree, so a section stays undirected.

### Derived direction and road geometry

The publish workflow can add, after the feed is built, a travel bearing worked out from these signals and
the OpenStreetMap road under each radar: `bearing` (integer, 0 to 359), `direction_semantics` (`"travel"`),
`direction_confidence` (`"high"` or `"low"`) and `bearing_source`. The road lines and the approach to each
radar are not in this file: they live in `roads.json` beside it. Both are described in `docs/roads.md`
once the roads step ships.

## DGT data: hosts and date

The DGT fixed-radar file is the same DATEX II file on two hosts, read in this order:

1. https://nap.dgt.es/datex2/dgt/PredefinedLocationsPublication/radares/content.xml
2. https://infocar.dgt.es/datex2/dgt/PredefinedLocationsPublication/radares/content.xml

The second is asked only when the first fails; the source fails only when both do, and then keeps its last
good result. The `url` of every DGT feature is the first host. The day in `, actualizado <day>` and in
`status.json` is the file's own `<publicationTime>`; the HTTP `Last-Modified` header only when the file
has none (NAP sends no such header). The date always describes the copy in use.

## Dropped before publishing

A DGT mobile-radar stretch whose two published ends cannot be that stretch is left out and logged: ends at
one place for a km range of several km, or ends far apart for a short range (the `dgt_invive.plausible`
rule for a straight line). No road can join such ends, so the feed would draw a wrong corridor.
