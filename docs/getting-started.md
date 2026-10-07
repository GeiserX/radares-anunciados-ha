# Getting started

You need Home Assistant 2026.6 or newer with the companion app (iOS or Android) on each phone that
should get alerts, and a machine that runs Docker.

## 1. Create a Home Assistant token

In Home Assistant, open your profile, then **Security → Long-lived access tokens → Create token**.
The service uses it to create, move and delete its radar zones.

## 2. Run the service

```yaml
# docker-compose.yml
services:
  radares-anunciados-ha:
    image: drumsergio/radares-anunciados-ha:0.3.0
    restart: unless-stopped
    environment:
      HA_URL: https://homeassistant.example.org
      HA_TOKEN: ${HA_TOKEN}
      # the phones to tell when the zones change (see step 4)
      RADARES_NOTIFY: notify.mobile_app_phone1,notify.mobile_app_phone2
      RADARES_CACHE: /data
    volumes:
      - ./data:/data
```

The container runs as user 65534, so give it the cache folder first: `mkdir -p data && sudo chown
65534:65534 data`. Then `docker compose up -d` and read the log. The first run creates the zones:

```
INFO Murcia list https://www.laopiniondemurcia.es/murcia/2026/09/28/...: 6 streets
INFO zones: 0 kept (0 with a new icon), 89 to create, 0 to delete
```

To see what it would do without touching Home Assistant:

```sh
docker compose run --rm radares-anunciados-ha sync --dry-run
```

## 3. Import the alert blueprint

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FGeiserX%2Fradares-anunciados-ha%2Fblob%2Fmain%2Fblueprints%2Fradar_zone_alert.yaml)

Or copy [`blueprints/radar_zone_alert.yaml`](../blueprints/radar_zone_alert.yaml) into Home Assistant
by hand. Create one automation from it and pick the phones' location trackers under **Phones**. Each
phone needs its own device name in the companion app. Its notify action is
`notify.mobile_app_<device name>`, so two phones with the same name share one action, and one of them
gets both phones' alerts. When a phone enters a radar zone, that phone gets a notification titled with
the radar, such as "Radar (límite 50)". On an iPhone it's time-sensitive, so it shows through Focus
modes; on Android it's a heads-up notification on the high-importance channel "Radares". Turn on
**Critical alert** to have it ring in silent mode: an iOS critical alert, or Android's alarm stream.

A street from a police list, or an average-speed section, is several zones with one name, so each
phone gets one alert for it and then stays quiet about it for the **Cooldown** (10 minutes by
default). Another radar, or another phone, is alerted at once. No helper is needed. If a push is lost
on the way to the phone, the rest of that street stays quiet too;
[how it works](how-it-works.md#one-alert-per-street) has the details.

### Updating from an earlier blueprint

The earlier blueprint had no **Phones** input; this one requires it. After you re-import the
blueprint, an automation made from the earlier one stops working: it shows as unavailable, and the log
says `Failed to generate automation from blueprint: Missing input phones`. Pick its phones to fix it:

1. Re-import the blueprint: **Settings → Automations & scenes → Blueprints**, open the menu of the "Radar
   ahead" blueprint and choose **Re-import blueprint**.
2. Open the automation made from it, pick each phone's location tracker under **Phones**, and save.

Sound, Time sensitive and Critical alert keep their values.

A blueprint imported before 0.3.0 points at the project's old address, which now belongs to the phone
app, so **Re-import blueprint** fails for it. Use the import button above instead and let it replace
your copy; later re-imports then work again.

## 4. Set up each phone

**iPhone.** Allow location **Always** and **Precise** for the app. The iOS app only downloads new
zones while it is open on screen. After every change the service sends "Radares actualizados" to the
phones in `RADARES_NOTIFY`; tap it and the new zones load. Until you do, the phone has only the zones
it loaded before: last week's streets, now silent, and none of this week's. If the app is already open
during a change, it loads the new zones on its own within half a minute. For **Critical alert**, allow critical alerts in iOS Settings → Notifications →
Home Assistant.

**Android.** In the app, **Settings → Companion app → Manage sensors**:

- turn on **Location zone** and **Background location**;
- under Background location, turn on **High accuracy mode** and limit it to "only when connected to
  BT devices", choosing the car's Bluetooth. It sends a location every 5 seconds, so the alert lands
  within a few seconds of the zone's edge. Without it, Android may report the entry minutes late, after
  the radar. It costs battery and shows a permanent notification, which is why it's limited to the car.

In Android's location settings keep **Google Location Accuracy** on: with it off, the app's geofences
fail without telling you. The app watches only the first 100 zones by entity id, not the nearest;
high accuracy mode is what makes every other radar alert. The app loads zones only when its process
starts or a location setting changes, so after a change turn **Location zone** off and on again, or
force-stop the app and reopen it.

If a street from the week's list can't be placed on the map, that notification names it after "Sin
aviso". That street gets no zone, so it gives no warning this week.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `HA_URL`, `HA_TOKEN` | | Home Assistant address and long-lived token |
| `RADARES_SOURCES` | all of them but `osm_notes` | which sources to use, such as `dgt,osm,murcia`; `default` stands for the default ones, so `default,osm_notes` adds the notes; keys in [Sources](sources.md) |
| `RADARES_PROVINCES` | `30` | INE province codes, such as `30` (Murcia) or `3,46`, or `all` for the whole country. `RADARES_DGT_PROVINCES` is the old name and still works |
| `RADARES_OSM_BBOX` | the provinces' boxes | `south,west,north,east` for OpenStreetMap cameras, or `all` for the whole country |
| `RADARES_FIXED_RADIUS` | `auto` | metres around a fixed radar; `auto` is 200 m plus 40 s at the speed limit |
| `RADARES_STREET_RADIUS` | `auto` | metres of each circle along an announced street; `auto` is 200 m plus 20 s at the limit |
| `RADARES_MAX_ZONES` | `1000` | most radar zones in Home Assistant, 1 or more; past it, the farthest fixed radars, the farthest stretch circles and the oldest silent streets get none |
| `RADARES_STRETCH_ZONES` | `off` | `on` gives zones along DGT's mobile-radar stretches; needs a province list in `RADARES_PROVINCES` |
| `RADARES_DORMANT_WEEKS` | `26` | weeks an announced street keeps its zones, silent, after its week; `0` deletes them when the week ends |
| `RADARES_SPANISH_IP_TIMEOUT` | | seconds; set it outside Spain so that a request to a host that answers only Spanish addresses gets one short try, not three of 90 s |
| `RADARES_NOTIFY` | | notify services told to open the app after a change |
| `RADARES_INTERVAL` | `3600` | seconds between runs |
| `RADARES_CACHE` | `~/.cache/radares-anunciados` | where downloads, each source's last good result and the announced streets are kept |
| `RADARES_METRICS_PORT` | `9464` | port of `/metrics` and `/healthz`; empty or `0` turns them off |

`radares feed` prints the merged list as GeoJSON, for anyone who wants the data without Home
Assistant; `--status FILE` also writes each source's state. The same feed for all of Spain is
[published every 6 hours](how-it-works.md#the-published-feed). [How it works](how-it-works.md) covers the sources and the zone logic.

## Know when it stops warning

A failing run, a source that stopped answering, a week without a list and a street it can't place on
the map all leave you without a warning while the container keeps running. [Alerting](alerting.md)
covers the `/metrics` and `/healthz` endpoints and has Prometheus alert rules for all four.
