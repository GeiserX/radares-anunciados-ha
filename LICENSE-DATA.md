# Data license

The code in this repository is under [GPL-3.0-or-later](LICENSE). This file covers the data: the
published feed (`feed.geojson`) and its `status.json`.

## The feed is under ODbL 1.0

The feed includes speed cameras mapped in OpenStreetMap, and places announced streets on OpenStreetMap
geometry. That makes it a database derived from OpenStreetMap, so it is offered under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).

You may copy, share and adapt it. If you publish it, or a database made from it, you must:

- credit it as "Radares Anunciados, © OpenStreetMap contributors and the sources listed below";
- offer what you publish under the ODbL too, and keep it open (no technical restrictions without an
  unrestricted copy alongside).

The ODbL covers the database as a collection. Each record in it stays under its own source's terms,
listed below, and you must respect those too when you reuse a record. Salamanca's records are under
the GNU Free Documentation License, which asks that a modified version be released under the GFDL as
well.

## Sources and their attributions

Every feature carries `source` and `attribution` properties, so each record keeps its credit when you
take it out of the feed. `status.json` lists the same attribution and licence for every source in the
build, and `updated`, the date the source gives for its last update, when it gives one.

| Source | What it gives | Attribution | Terms |
|---|---|---|---|
| `dgt` | fixed radars and average-speed sections from the [DGT National Access Point](https://nap.dgt.es/dataset/radares-fijos-dgt) | Dirección General de Tráfico, with the file's last update date | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `osm` | `highway=speed_camera` nodes and `type=enforcement` relations from [OpenStreetMap](https://www.openstreetmap.org/copyright) | © OpenStreetMap contributors | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) |
| `osm_notes` | open OpenStreetMap [notes](https://wiki.openstreetmap.org/wiki/Notes) that report a speed camera, unconfirmed | © OpenStreetMap contributors | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), see below |
| `murcia` | the weekly mobile-radar list of the Policía Local de Murcia, read in the local press, placed on OpenStreetMap streets | Policía Local de Murcia, and the newspaper it was read in; geometry © OpenStreetMap contributors | the facts of the police's list (street, district, week) as the press reports them, not the article's text (see below); geometry ODbL 1.0 |
| `dgt_invive` | stretches of road where DGT runs mobile radars, from the [DGT National Access Point](https://nap.dgt.es/es/dataset/tramos-invive); road geometry from OpenStreetMap | Dirección General de Tráfico; geometry © OpenStreetMap contributors | Creative Commons Attribution, as the dataset page states it with no version; geometry ODbL 1.0 |
| `sct` | fixed and section radars in Catalonia, from the [Servei Català de Trànsit](https://transit.gencat.cat/ca/seguretat_viaria/cinemometres-fixos-trams-mobils/) | Generalitat de Catalunya. Departament d'Interior i Seguretat Pública. Servei Català de Trànsit, with the file's last update date | Llicència oberta d'ús d'informació – Catalunya |
| `sct_remolc` | the published spots for trailer radars in Catalonia, same publisher | Generalitat de Catalunya. Departament d'Interior i Seguretat Pública. Servei Català de Trànsit, with the file's last update date | Llicència oberta d'ús d'informació – Catalunya |
| `euskadi` | fixed and section radars of the Basque Country | Gobierno Vasco / Eusko Jaurlaritza, Dirección de Tráfico (Trafikoa) | no reuse licence; the data is reused under Ley 37/2007 (see below). The [euskadi.eus legal notice](https://www.euskadi.eus/informacion/-/informacion-legal) reserves the site's content |
| `navarra` | fixed radars of the Navarra traffic viewer that the DGT file lacks | Gobierno de Navarra, Visor de Tráfico | no reuse licence; the data is reused under Ley 37/2007 (see below). The [navarra.es legal notice](https://www.navarra.es/es/aviso-legal) reserves the site's content |
| `donostia` | municipal fixed radars of Donostia / San Sebastián | © Donostiako Udala - Ayuntamiento de Donostia / San Sebastián | the council's own terms, in section 3 of its [legal notice](https://www.donostia.eus/es/aviso-legal): reuse allowed "siempre que se mantenga íntegro el contenido, se cite la fuente y no se utilice para fines ilícitos (Ley 37/2007 ...)"; third-party content excluded |
| `donostia_movil` | Donostia's mobile-radar streets for the day | Ayuntamiento de Donostia / San Sebastián, ubicación del radar móvil | the council's own terms, as for `donostia` |
| `madrid` | Madrid city fixed and section radars, from [datos.madrid.es](https://datos.madrid.es/dataset/300049-0-radares-fijos-moviles) | Origen de los datos: Ayuntamiento de Madrid, with the dataset's last update date | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `salamanca` | Salamanca city fixed and section radars, from its [open data portal](https://opendata.aytosalamanca.es/datosabiertos/catalogo/dataset/radares-fijos) | Ayuntamiento de Salamanca, Radares Municipales, with each layer's last update date | GNU Free Documentation License, as the dataset states it; the portal adds: data unaltered, source cited, date of last update given |
| `leon` | León's mobile-radar streets for each day, placed on OpenStreetMap streets | Ayuntamiento de León; on days only iLeón covers, Redacción ILEÓN, obtenido de ILEÓN (ileon.eldiario.es); geometry © OpenStreetMap contributors | the council's posts: no reuse licence; the data is reused under Ley 37/2007 (see below); its portal's [terms](https://www.aytoleon.es/es/inicio/Paginas/terminos-y-condiciones-generales.aspx) reserve reproduction except for personal and private use. Days only iLeón covers: the facts iLeón reports (street, day, limit), not its text, which is CC BY-NC 4.0 (see below). Geometry ODbL 1.0 |
| `barcelona_multas` | places where Barcelona's mobile radars fined, from the city's [traffic-fines open data](https://opendata-ajuntament.barcelona.cat/data/es/dataset/denuncies_sancions_transit_bcn_detall), grouped by place and day | Fuente de los datos: Ayuntamiento de Barcelona, with the quarter and the date of the last update | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `madrid_multas` | places where Madrid's mobile radars fined in two months or more, from the city's [traffic-fines open data](https://datos.madrid.es/dataset/210104-0-multas-circulacion-detalle), placed at their address in the city's [official street register](https://datos.madrid.es/dataset/213605-0-callejero-oficial-madrid) | Origen de los datos: Ayuntamiento de Madrid, with the months and the date of the last update | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

## Public bodies with no reuse licence

The Basque Government (`euskadi`), the Government of Navarra (`navarra`) and the León council
(`leon`, its own monthly posts) publish their radar lists with no reuse licence, and their sites'
legal notices reserve the content. The feed reuses the data in those lists under Spain's law on the
reuse of public-sector information,
[Ley 37/2007, de 16 de noviembre](https://www.boe.es/buscar/act.php?id=BOE-A-2007-19814), as
public-sector information. What the law says:

- Who: the State, regional and local administrations (art. 2.a). The law is basic legislation, so it
  applies to all of them (first final provision).
- What reuse is: the use of their documents by anyone, for commercial or non-commercial purposes
  other than the public-service purpose they were made for (art. 3.1).
- Which documents: all, except those art. 3.3 lists. We considered two exclusions. Documents on
  public security (3.3.b) do not apply: these bodies publish the positions to the public so that
  drivers know them. Documents on which third parties hold intellectual property (3.3.e) do not
  apply to the lists themselves; they would apply to a newspaper's article, which is why the feed
  takes facts only from the press (below).
- Reusable: their documents "serán reutilizables en los términos previstos en esta ley" (art. 4.1),
  under one or more of these modalities: (a) documents made available to the public with no
  conditions; (b) with conditions set in standard licences; (c) on prior request, by the procedure
  of art. 10; (d) exclusive agreements. The feed treats these openly published lists as modality
  (a). It has not asked any of these bodies for reuse under (c).
- Conditions: reuse is subject to conditions only when they are objective, proportionate,
  non-discriminatory and justified by a public interest, and such conditions are set in a licence
  (art. 4.2).

Donostia / San Sebastián publishes its own reuse terms under the same law: section 3 of its
[legal notice](https://www.donostia.eus/es/aviso-legal) allows reuse keeping the content whole,
citing the source and not for unlawful ends, and excludes third-party content.

Art. 8 lists the general conditions a body may attach to reuse. The feed meets them for every
source, with or without a licence:

| Art. 8 | Condition | How the feed meets it |
|---|---|---|
| a | the content, metadata included, is not altered | values are not changed: positions, roads, km, streets, days and limits are the source's. The feed transforms them only in form: positions given in ETRS89 UTM (SCT, Basque Government, Navarra) are reprojected to WGS84 longitude and latitude; abbreviations in street names are spelled out ("Avda." becomes "Avenida"); duplicates are dropped (one camera per site in Madrid, the Navarra radars the DGT file also lists, an OpenStreetMap camera within 150 m of an official radar, an OpenStreetMap section near a published one). The feed never repairs a position; it skips a record with broken coordinates. It adds a radius and, for an announced street, circles along it; street geometry and a limit the source does not give come from OpenStreetMap and are credited to it |
| b | the meaning is not distorted | each record keeps its kind and the days it is valid on; a street whose period has ended is marked `active: false`, never shown as announced |
| c | the source is cited | every feature carries `source` and `attribution`; `status.json` gives each source's attribution and terms |
| d | the date of the last update is given | where the source gives one (the DGT and SCT file dates, the Madrid and Salamanca catalogue dates), each record's attribution carries "actualizado" and the date, and `status.json` gives it as `updated`. The Basque, Navarra, Donostia, Murcia and León lists give none; for every source, `status.json` gives `data_time`, when its data was read, and a daily or weekly list carries the days it is valid on (`valid_from`, `valid_to`) |
| e, f | personal data | none; the records are places, roads and dates |

The feed does not say or suggest that any source takes part in it or endorses it. Art. 4.9 forbids that
for the State's bodies.

## Lists read in the press

The Policía Local de Murcia posts its weekly mobile-radar list on its social networks as an image,
which a program cannot read without an account. The feed reads the list in the press instead, in La
Opinión de Murcia (Murcia Actualidad as a fallback). On days the León council's own post does not
cover, the feed reads León's list in iLeón, whose articles are CC BY-NC 4.0. In both cases the feed
does not reuse a public-sector document it never reads, and it takes nothing of the article's text.
It takes the facts of the list (street, district or day, limit) and credits the newspaper, by name in
each radar's attribution, as where the list was read.

## Sources added later

OpenStreetMap publishes its notes, anonymous ones included, with the rest of its database at
https://planet.openstreetmap.org/, where every file published after 12 September 2012 is under ODbL 1.0.
A note written from an account is a contribution under the OpenStreetMap
[Contributor Terms](https://osmfoundation.org/wiki/Licence/Contributor_Terms), like a mapped camera.

A source added later carries its own attribution and terms in its module (`sources/<key>.py`), in every
feature it adds and in `status.json`; it is added to the table of sources in the same change.

The map tiles on the published page are © OpenStreetMap contributors and are not part of the feed.

## What the feed is

Positions published by the sources above, merged and deduplicated, plus the unconfirmed reports of
`osm_notes` (kind `reported`), which no source has confirmed and which never become a zone. It warns
from published positions only and is offered as is, with no warranty: a radar can be missing, moved or
out of date.
