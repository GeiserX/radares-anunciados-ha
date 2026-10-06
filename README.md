<p align="center">
  <img src="docs/images/banner.svg" alt="Radares Anunciados" width="100%">
</p>

<h1 align="center">Radares Anunciados</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/GeiserX/radares-anunciados-ha?style=flat-square" alt="License"></a>
</p>

Radares Anunciados is a Docker service that gathers the speed radars announced in Spain into one open GeoJSON feed. Its Home Assistant side turns the radars near you into zones, so the Home Assistant companion app alerts you when you drive toward one.

This repo is the data side: the sources, the open feed and the Home Assistant zones. The phone app (iPhone now, Android later) lives at https://github.com/GeiserX/radares-anunciados and reads this feed.

## Features

- One GeoJSON feed with every announced radar, each point tagged with its source and license
- The mobile-radar lists that councils publish: Murcia (weekly), León (monthly, every day) and Donostia (daily)
- Fixed, section and mobile-stretch radars from the DGT, the Servei Català de Trànsit, the Basque and Navarra governments, and the cities of Madrid, Salamanca and Donostia
- Where Barcelona's and Madrid's traffic fines show mobile radars stood, from the cities' open data
- Speed cameras and average-speed sections mapped in [OpenStreetMap](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dspeed_camera), and, on the published map, the open OpenStreetMap notes that report a new one, shown as unconfirmed and never turned into zones
- Home Assistant zones kept in sync with the radars around you, for alerts through the companion app
- Warns from published positions only; it never senses or jams a radar signal

## Quick start

```sh
export HA_URL=https://homeassistant.example.org HA_TOKEN='<long-lived token>'
docker run --rm -e HA_URL -e HA_TOKEN drumsergio/radares-anunciados-ha:0.3.0 sync --dry-run
mkdir -p data && sudo chown 65534:65534 data  # the container runs as nobody
docker run -d --name radares -e HA_URL -e HA_TOKEN -v ./data:/data -e RADARES_CACHE=/data drumsergio/radares-anunciados-ha:0.3.0
```

Then import the [alert blueprint](blueprints/radar_zone_alert.yaml). Full steps in [Getting started](docs/getting-started.md).

## Open feed

Every 6 hours a GitHub Actions run on a self-hosted runner in Spain builds the feed for all of Spain from
every source, including those that answer only Spanish addresses, and publishes it on GitHub Pages, at
[geiserx.github.io/radares-anunciados-ha](https://geiserx.github.io/radares-anunciados-ha/):

- [`feed.geojson`](https://geiserx.github.io/radares-anunciados-ha/feed.geojson): every radar as a point and every
  watched stretch as a line, each with its source, attribution, limit and link
- [`status.json`](https://geiserx.github.io/radares-anunciados-ha/status.json): per source, `ok`, `stale` (this run
  failed, its last good copy is used) or `missing`, with record counts, the time of its data and the
  source's own last update date when it gives one
- a map of the feed

The feed includes OpenStreetMap data, so the database is offered under
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) with each source's attribution: see
[LICENSE-DATA.md](LICENSE-DATA.md). Data from public bodies is reused under
[Ley 37/2007](https://www.boe.es/buscar/act.php?id=BOE-A-2007-19814) on the reuse of public-sector
information, with the source cited, values unchanged, and the source's last update date where the
source gives one.
[How it works](docs/how-it-works.md#the-published-feed) has the details.

## Documentation

- [Getting started](docs/getting-started.md): token, container, blueprint, settings
- [How it works](docs/how-it-works.md): sources, street matching, the 20-zone limit, passive zones
- [Alerting](docs/alerting.md): Prometheus metrics, the health check, alert rules for a failing service
- [Sources](docs/sources.md): every source, its licence and cadence, and what we checked that publishes nothing usable

## Data sources

| Source | License |
|---|---|
| [DGT NAP](https://nap.dgt.es/dataset/radares-fijos-dgt): fixed radars, sections and mobile-radar stretches | Creative Commons Attribution |
| [Servei Català de Trànsit](https://transit.gencat.cat/ca/seguretat_viaria/cinemometres-fixos-trams-mobils/): fixed, section and trailer radars | Llicència oberta d'ús d'informació – Catalunya |
| [Madrid](https://datos.madrid.es/dataset/300049-0-radares-fijos-moviles) and [Salamanca](https://opendata.aytosalamanca.es/datosabiertos/catalogo/dataset/radares-fijos) open data | CC BY 4.0; GNU FDL |
| [Barcelona](https://opendata-ajuntament.barcelona.cat/data/es/dataset/denuncies_sancions_transit_bcn_detall) and [Madrid](https://datos.madrid.es/dataset/210104-0-multas-circulacion-detalle) traffic-fines open data, and Madrid's [street register](https://datos.madrid.es/dataset/213605-0-callejero-oficial-madrid): where mobile radars fined, months ago | CC BY 4.0 |
| Basque and Navarra governments, León council | no reuse licence; reused under [Ley 37/2007](LICENSE-DATA.md#public-bodies-with-no-reuse-licence) |
| Donostia council | its own reuse terms, in its [legal notice](https://www.donostia.eus/es/aviso-legal) |
| Murcia's Policía Local list, León's on days only iLeón covers | facts only, [read in the press](LICENSE-DATA.md#lists-read-in-the-press) |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors: cameras, sections, notes, speed limits, street geometry | ODbL 1.0 |

The full table, with cadence and which sources need a Spanish IP, is in [docs/sources.md](docs/sources.md).

## Legal

In Spain, [RGC art. 18.3](https://www.boe.es/buscar/act.php?id=BOE-A-2003-23514#a18) bans radar jammers and radar detectors in a vehicle, and excludes from that ban the warning mechanisms that report where traffic enforcement systems are. Radar positions here come only from published lists and maps, so this project is one of those warning mechanisms.

## License

Code: [GPL-3.0-or-later](LICENSE). Data in the published feed: [ODbL 1.0](LICENSE-DATA.md).
