# Sources

Every radar in the feed keeps the source it came from and that source's attribution, with the source's
last update date where the source gives one. `RADARES_SOURCES` picks sources by key (default: all of
them but `osm_notes`; `default,osm_notes` adds it), and a source that covers none of the selected provinces is
skipped. A source marked "yes" under Spanish IP answers only to requests from a Spanish address. Run the service
from Spain to use it. Anywhere else that source shows as down and keeps its last good result.

| Key | What it gives | Publisher | Licence | Cadence | Spanish IP |
|---|---|---|---|---|---|
| `dgt` | Fixed and average-speed section radars on the roads DGT polices: position, road, km, direction. Each fetch also reads the file's `Last-Modified` and warns once it is 30 days old | Dirección General de Tráfico, [NAP dataset radares-fijos-dgt](https://nap.dgt.es/dataset/radares-fijos-dgt), DATEX II | Creative Commons Attribution, as the NAP page states it with no version; recorded as CC BY 4.0. The attribution carries the file's last update date | downloaded daily; the file last changed on 18 Dec 2025 | no |
| `dgt_invive` | About 1,330 stretches of conventional road where DGT runs mobile radars, in 43 provinces (none in Catalonia or the Basque Country): road, km range, both ends. A line in the feed; zones along the road only with `RADARES_STRETCH_ZONES=on` and a province list | Dirección General de Tráfico, [NAP dataset tramos-invive](https://nap.dgt.es/es/dataset/tramos-invive), DATEX II | Creative Commons Attribution, as the NAP page states it with no version; terms of use https://www.dgt.es/contenido/aviso-legal/. Road geometry: ODbL 1.0 | NAP updates it every 4 months; downloaded daily; road geometry from OpenStreetMap cached 90 days | no |
| `osm` | Speed cameras mapped as [`highway=speed_camera`](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dspeed_camera), with limit and direction when tagged. The [enforcement relations](https://wiki.openstreetmap.org/wiki/Relation:enforcement) around them add a limit and a direction where the camera has none, and the average-speed sections: both ends and a line between them. The relations come in a second query; when it fails, the last relations answer is used, and with none the cameras come alone. A camera within 150 m of an official radar is dropped as a copy. A section goes whole, line and both ends, when one end is within 1 km of an end of a published section or within 150 m of a published radar | OpenStreetMap contributors | ODbL 1.0 | daily | no |
| `osm_notes` | Open [notes](https://wiki.openstreetmap.org/wiki/Notes) in which someone reports a speed camera, usually one nobody has mapped yet: position, date, text and link. Unconfirmed: in the feed and on the map only, never a zone. Off unless `RADARES_SOURCES` names it; the published feed does | OpenStreetMap contributors | ODbL 1.0 | each published feed run; a closed note leaves with the next download | no |
| (limit lookup) | The `maxspeed` of the road under each radar whose source gives no limit, which sets the radius. Not a radar source | OpenStreetMap contributors | ODbL 1.0, added to the attribution of each radar it sets | each position asked again after 30 days; the old answer stays if that fails | no |
| `murcia` | Murcia's Policía Local weekly mobile-radar list: street and district, placed on the map from OpenStreetMap | Policía Local de Murcia, read in La Opinión de Murcia (Murcia Actualidad as a fallback), which each radar credits | the facts of the Policía Local's list (street, district, week) as the press reports them, not the article's text; the police post the list only as an image on social networks ([LICENSE-DATA.md](../LICENSE-DATA.md#lists-read-in-the-press)). Geometry: ODbL 1.0 | weekly | no |
| `sct` | Fixed radars and the cameras of section radars in Catalonia (provinces 08, 17, 25, 43): road, km, speed limit; no direction | Servei Català de Trànsit, [radars.txt](https://transit.gencat.cat/web/.content/documents/seguretat_viaria/radars.txt) | Llicència oberta d'ús d'informació – Catalunya; the attribution carries the file's last update date | republished irregularly (last on 17 Sep 2026); downloaded daily | no |
| `sct_remolc` | The published spots where a trailer radar can stand in Catalonia: road, km, speed limit | Servei Català de Trànsit, [radars-remolc.txt](https://transit.gencat.cat/web/.content/documents/seguretat_viaria/radars-remolc.txt) | Llicència oberta d'ús d'informació – Catalunya; the attribution carries the file's last update date | republished irregularly; downloaded daily | no |
| `euskadi` | Fixed booths and section radars of the Basque Country (Araba, Gipuzkoa, Bizkaia), with speed limits | Gobierno Vasco, Dirección de Tráfico (Trafikoa) | no reuse licence; the data is reused under [Ley 37/2007](https://www.boe.es/buscar/act.php?id=BOE-A-2007-19814) as public-sector information ([LICENSE-DATA.md](../LICENSE-DATA.md#public-bodies-with-no-reuse-licence)). The [euskadi.eus legal notice](https://www.euskadi.eus/informacion/-/informacion-legal) reserves the site's content | daily | yes |
| `navarra` | Fixed radars of the Navarra traffic viewer. The ones the DGT file already lists (same road, within 0.5 km) are left to `dgt`, so it adds only what DGT lacks | Gobierno de Navarra, Visor de Tráfico | no reuse licence; the data is reused under [Ley 37/2007](https://www.boe.es/buscar/act.php?id=BOE-A-2007-19814) as public-sector information ([LICENSE-DATA.md](../LICENSE-DATA.md#public-bodies-with-no-reuse-licence)). The [navarra.es legal notice](https://www.navarra.es/es/aviso-legal) reserves the site's content | daily | yes |
| `donostia` | Municipal fixed radars of Donostia / San Sebastián, with speed limits | Ayuntamiento de Donostia / San Sebastián, GeoDonostia map layer 41 | the council's own reuse terms, section 3 of its [legal notice](https://www.donostia.eus/es/aviso-legal): content kept whole, source cited, no unlawful use (Ley 37/2007); third-party content excluded | daily | no |
| `donostia_movil` | Donostia's mobile-radar plan for the day: the day's streets as circles along the council map's lines, valid that day only | Ayuntamiento de Donostia / San Sebastián, ["Ubicación del radar móvil"](https://www.donostia.eus/info/ciudadano/radar_movil.nsf/fwHome?ReadForm=&idioma=cas&id=A434305381910) | the council's own reuse terms, section 3 of its [legal notice](https://www.donostia.eus/es/aviso-legal): content kept whole, source cited, no unlawful use (Ley 37/2007); third-party content excluded | hourly | no |
| `madrid` | Madrid city fixed and section radars, one per camera site, with limit and direction; no mobile radars | Ayuntamiento de Madrid, [datos.madrid.es dataset 300049](https://datos.madrid.es/dataset/300049-0-radares-fijos-moviles) | CC BY 4.0, cited as "Origen de los datos: Ayuntamiento de Madrid" with the date of the last update | updated occasionally; downloaded daily | no |
| `salamanca` | Salamanca city fixed radars with limits, and section radars as lines | Ayuntamiento de Salamanca, [Radares Municipales](https://opendata.aytosalamanca.es/datosabiertos/catalogo/dataset/radares-fijos) | GNU Free Documentation License, as the dataset states it; the portal's terms add data unaltered, source cited, date of last update given (each layer's date is in the attribution) | updated occasionally; downloaded daily | no |
| `leon` | León's mobile radars for every day of the month: 5 streets a shift, two shifts a day, each with its limit, valid on its own day and placed inside León's municipal border | Ayuntamiento de León, monthly post; for days no post covers, the weekly article of [iLeón](https://ileon.eldiario.es) | Ayuntamiento de León's posts: no reuse licence; the data is reused under [Ley 37/2007](https://www.boe.es/buscar/act.php?id=BOE-A-2007-19814) as public-sector information ([LICENSE-DATA.md](../LICENSE-DATA.md#public-bodies-with-no-reuse-licence)); its portal's [terms](https://www.aytoleon.es/es/inicio/Paginas/terminos-y-condiciones-generales.aspx) reserve reproduction except for personal and private use. Days only iLeón covers: the facts iLeón reports, not its text, which is CC BY-NC 4.0; each radar credits iLeón. Geometry: ODbL 1.0 | monthly (council), weekly (iLeón) | yes |
| `barcelona_multas` | Spots where a mobile radar fined in Barcelona in the newest quarter of the city's traffic fines (kind `mobile_recurring`). A place counts when it fined on 2 days or more, in sessions of at least 2 fines, and not like a fixed camera: fines spanning 12 hours or more on over a quarter of its days, more than 7 days in a row, or more than half the quarter's days. A session that runs past midnight counts once, and places of one street within 200 m are one spot. In the last quarter of 2025 this kept 13 of 53 camera places. 4 of them sit within 150 m of a mapped camera, which already warns there, so 9 zones remain | Ajuntament de Barcelona, Institut Municipal d'Hisenda, [Open Data BCN dataset denuncies_sancions_transit_bcn_detall](https://opendata-ajuntament.barcelona.cat/data/es/dataset/denuncies_sancions_transit_bcn_detall), read through its datastore SQL API | CC BY 4.0, cited as "Fuente de los datos: Ayuntamiento de Barcelona" with the date of the last update; each radar's attribution names the quarter and the days the place fined | quarterly, about nine months behind. On 2 Oct 2026 the newest quarter was October to December 2025. Checked weekly | no |
| `madrid_multas` | Spots where Madrid's mobile radars fined in 2 or more of the last 6 months (kind `mobile_recurring`), at the street number the fines name, such as `N378 AV ARAGON`, placed at that address in the city's official street register. A place that fines in 18 or more distinct hours of one month is a camera and gets no zone. Places of one street within 200 m are one spot. A place coded `F<number>` is a lamp post, which the city publishes by no number, so it gets no zone (3,800 to 7,000 speed fines a month). September 2025 to February 2026: 124 numbered places, 33 recurring, all 33 placed (within 92 m of CartoCiudad's point for the same address, median 6 m), 27 spots. One sits within 150 m of a mapped camera, so 26 zones remain | Ayuntamiento de Madrid, [datos.madrid.es dataset 210104](https://datos.madrid.es/dataset/210104-0-multas-circulacion-detalle) and the [official street register, dataset 213605](https://datos.madrid.es/dataset/213605-0-callejero-oficial-madrid) | CC BY 4.0 both, cited as "Origen de los datos: Ayuntamiento de Madrid" with the date of the last update; each radar's attribution names the months and how many of them the place fined in | monthly, about seven months behind. On 2 Oct 2026 the newest month was February 2026. Checked weekly; each month's 60 MB file is read once, and the 35 MB register only when a new place needs it | no |

## Unconfirmed reports

A point of kind `reported` (from `osm_notes`) is a report by one person: no authority published it and
no mapper has checked it yet. It may be wrong, misplaced or about a camera that is gone. It shows on the
map in its own colour, with the note's date, its text (cut at 200 characters) and a link to the note.
It never becomes a Home Assistant zone, and it never drops, merges with or replaces another radar: a
note next to a published radar stays as it is. `status.json` counts these points as `reported`, not as
radars.

The source asks OpenStreetMap's notes API for every open note in the world that mentions "radar",
225 notes on 2 Oct 2026, and keeps the ones in Spain. The province boxes reach into Portugal, France
and Andorra. Where only the Catalan boxes reach, a note must also fall inside Catalonia's outline
([`catalonia_shapes.py`](../src/radares_anunciados/sources/catalonia_shapes.py)), which drops Andorra and
the French Pyrenees. Along the borders with Portugal, and with France west of Catalonia, the boxes are
all the project has, so a note just across the border stays in; none was there on 2 Oct 2026. The
source also drops notes about red-light cameras, cameras that are gone, radar stations and antennas,
speed displays, "RADAR key" toilets, and anything a radar detector found. It drops a note that names
or links a commercial radar app or its community (Comunidad Radar at lincegps.com, Radarbot, Waze,
Coyote and a few more listed in the module): that is the app's data, which the project does not use.
On 2 Oct 2026 it kept 35 notes, all in Spain. It dropped 16 in Andorra, 1 in France, 3 red-light
cameras in Girona and 1 that cites lincegps.com.

The OSMF [API usage policy](https://operations.osmfoundation.org/policies/api/) says: "The editing API
is provided in order to edit the map data, not for read-only purposes or projects. Clients may be
blocked without notice if they are affecting the service level for others or causing data
corruption." The API is the only place that serves notes besides the daily
[notes dump](https://planet.openstreetmap.org/notes/), which weighs 409 MB. So there is one reader for
the whole project: `osm_notes` is off by default, and only the published feed turns it on, with
`RADARES_SOURCES: default,osm_notes` in the build step that both jobs of
[`feed.yml`](../.github/workflows/feed.yml) share
([`.github/actions/build-feed`](../.github/actions/build-feed/action.yml)). That is one request of
about 230 KB per run, four a day plus one per pull request build, with the project's User-Agent. Private installs do not read notes: they would gain nothing, because
notes never become zones. A failed request keeps the last good result.

## Gaps we know about

- The DGT fixed-radar file has not changed since 18 Dec 2025, while DGT's own PDF list is newer. Radars
  added since then are missing unless OpenStreetMap maps them.
- `radars.txt` ships rows with broken coordinates. On 1 Oct 2026 that was 17 of 247 rows, 13 of them
  section cameras. The source skips them and never guesses a fix. If a file loses more than a third of
  its rows, the source refuses it and keeps the last good result.
- Catalonia's table of mobile-radar stretches gives a road and a km range only. Placed from
  OpenStreetMap's km markers, 1 point in 9 landed more than 300 m off, and up to 20 km off where one
  road number carries two km sequences. We don't draw it. Its section-radar table gives a road and a
  town, no position.
- With `RADARES_SOURCES=navarra` and no `dgt`, the Navarra radars DGT lists get no zone.
- Speed limits from the limit lookup make the feed carry ODbL data even for a DGT-only setup.
- OpenStreetMap maps most average-speed sections with no km, so their zones are named after the road
  of the section's ways and the relation id, such as `Radar de tramo A-7 (OSM 10026551)`, or the id
  alone when no way has a road. The blueprint alerts once per name, and the A-7 alone has 8 mapped
  sections: a name without the id would leave the next one silent.
- A mapped section near a published one is the same section with its ends placed elsewhere: up to
  536 m apart on the A-7 at Lorca. So a section with one end within 1 km of a published section end
  goes whole. On 2 Oct 2026 that left 200 of the 305 mapped sections. The cost: a mapped section that
  starts where a published one ends, and that no authority publishes, goes too.
- The fines sources run months behind, about nine for `barcelona_multas` and seven for `madrid_multas`.
  They show where radars stood then, not where one stands today. Each radar's attribution, which the
  map shows, names the period.
- A fixed camera that fines a car or two a day, never more than 7 days in a row and on under half the
  quarter's days, looks like a mobile radar to `barcelona_multas`. Carrer de Mallorca 351 (41 days) does.
  A mapped camera 23 m away keeps it out of the feed; an unmapped one would get a zone.
- The 150 m rule needs the camera sources to have answered. While one with no last good result fails
  (an Overpass 504 on a first run), the fines spots in its provinces wait for a later run, and the log
  says so. A source left out of `RADARES_SOURCES` holds nothing back: with `osm` left out, a spot next
  to a mapped camera gets a zone.
- A Madrid place that `madrid_multas` cannot place is logged only. Unlike a police list's street, it is
  not exported in `/metrics` nor named in the notification.

## Checked, nothing usable

These publish no radar positions or schedule we can read, or publish them in a form we cannot use.

Authorities:

- DGT's PDF "Puntos y tramos de control de velocidad", on its
  [enforcement page](https://www.dgt.es/conoce-el-estado-del-trafico/vigilancia-y-control/equipos-y-tramos-de-vigilancia/index.html),
  gives road and km only, no coordinates, and its terms forbid reuse.
- DGT's live incident feed and its etraffic map: no radar positions.
- Basque traffic API and Open Data Euskadi: no radars. Trafikoa's campaign page gives dates only.
- Regional open-data portals of the Comunitat Valenciana, the Comunidad de Madrid, Andalucía, Aragón,
  Castilla y León, Galicia and Canarias: no radar dataset.
- Road owners outside Catalonia, the Basque Country and Navarra (Xunta, cabildos, consells,
  diputaciones) publish speed-limit signs, not radars. DGT polices their interurban roads, and its two
  files above cover them.

Cities, north and centre:

- Zaragoza, Valladolid, Logroño, Bilbao: campaign notices with no streets.
- Vitoria-Gasteiz, Palencia, Majadahonda, Albacete: they say on purpose that the radar is not announced.
- Burgos: a 2018 list of ten streets where the mobile radar may stand, no schedule.
- Zamora: a 2016 list of candidate points, no schedule.
- Ponferrada: streets per day in prose, only during DGT campaign weeks, in the press.
- Pamplona: the fixed-radar page refuses automated requests; no mobile schedule.
- Gijón, Oviedo, Avilés, Santander, Torrelavega, Barakaldo, Getxo, Huesca, Teruel, Segovia, Soria,
  Ávila: nothing, or fixed-radar news only.
- Galicia (A Coruña, Vigo, Ourense, Lugo, Pontevedra, Santiago, Ferrol): fixed-radar news only.
- The Comunidad de Madrid towns (Móstoles, Alcalá, Fuenlabrada, Leganés, Getafe, Alcorcón, Torrejón,
  Parla, Alcobendas, Las Rozas, San Sebastián de los Reyes, Pozuelo, Rivas, Coslada, Valdemoro,
  Aranjuez): nothing.
- Castilla-La Mancha (Toledo, Talavera, Ciudad Real, Guadalajara, Cuenca, Puertollano) and Extremadura
  (Badajoz, Cáceres, Mérida, Plasencia): campaign notices only.

Cities, Mediterranean, south and islands:

- Cartagena: a weekly street list on the council site from 2019 to May 2021, stopped since.
- Molina de Segura: weekly posts on social networks only. Lorca: nothing.
- Santa Cruz de Tenerife and Las Palmas de Gran Canaria: the day's streets as posts on X only, which we
  cannot read.
- València, Alicante, Castelló, Granada, Barcelona, Lleida, Terrassa, Sabadell: fixed radars in one-off
  press releases, no dataset and no mobile schedule. OpenStreetMap maps most of them.
- Málaga: open data has red-light cameras only.
- Huelva, Jaén, Algeciras, Chiclana, Telde, Tarragona, Melilla, Eivissa / Sant Josep, Maó, Ciutadella:
  DGT campaign weeks, usually with no streets.
- Elche, Torrevieja, Orihuela, Benidorm, Gandia, Torrent, Paterna, Sagunt, Sevilla, Córdoba, Almería,
  Cádiz, Jerez, Marbella, Dos Hermanas, San Fernando, Roquetas, El Ejido, the Costa del Sol towns,
  Motril, Linares, Palma, La Laguna, Arona, Ceuta, Reus, Girona, L'Hospitalet, Badalona, Mataró,
  Santa Coloma: nothing.
