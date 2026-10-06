# Alerting

Four failures leave you driving past a radar with no warning, and none of them stops the container:

- A street from the weekly list can't be placed on the map, so it gets no zone.
- No list is found for this week, because the newspaper changed its page or published late.
- A source stops answering. The run still succeeds: the source's last good result keeps its zones, but
  new radars from it never arrive.
- Every run fails, because Home Assistant is unreachable, and the phones keep the zones they have.

`radares run` exposes its state over HTTP so any alerting stack can catch these. This page shows the
[Prometheus](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/) and
Alertmanager setup. The container itself is set up in [Getting started](getting-started.md).

## Endpoints

`radares run` serves both on `RADARES_METRICS_PORT`, which defaults to `9464`. Empty or `0` turns them off.

| Path | Answers |
|---|---|
| `/metrics` | Prometheus text format, the metrics below |
| `/healthz` | `200 ok: …` while the last successful run is at most 3 intervals old, `503 stale: …` after that. Before the first success it counts from the start of the process |

The image has a `HEALTHCHECK` that runs `radares health`, which asks `/healthz`. `docker ps` shows the
container as `unhealthy` once runs have failed for 3 intervals, which is 3 hours with the default
`RADARES_INTERVAL`. With the metrics port off, the check always passes.

If the server can't start, because the port is taken or `RADARES_METRICS_PORT` is not a port number,
`radares run` logs an error and keeps syncing without metrics. `radares health` then fails, since
nothing answers `ok` on that port, so the container shows as `unhealthy` until the setting is fixed. The
warnings keep working meanwhile; only the alerting is blind.

To let Prometheus scrape it from another host, publish the port:

```yaml
services:
  radares-anunciados-ha:
    # ...as in Getting started
    ports:
      - "9464:9464"
```

## Metrics

| Metric | Labels | Meaning |
|---|---|---|
| `radares_build_info` | `version` | always 1 |
| `radares_interval_seconds` | | seconds between runs |
| `radares_last_run_timestamp_seconds` | | when the last run ended, failed or not |
| `radares_last_success_timestamp_seconds` | | when the last run that synced Home Assistant ended |
| `radares_consecutive_failed_runs` | | runs failed in a row; 0 after a success |
| `radares_radars` | `source` | radars per source in the last collected list |
| `radares_sync_zones` | `action` (`kept`, `created`, `deleted`, `updated`) | what the last sync did; `updated` counts kept zones whose icon changed |
| `radares_zones_left_out` | | radars with no zone because there were more than `RADARES_MAX_ZONES` |
| `radares_source_up` | `source` | 1 if the source answered in the last run, 0 if it failed and its last good result (if any) was used |
| `radares_source_data_age_seconds` | `source` | seconds since the source last answered; a download still inside the source's cache age counts as an answer. Keeps growing while it fails; absent for a source that never answered |
| `radares_weekly_list_found` | `source` | 1 if this week's police list was found, else 0 |
| `radares_weekly_list_missing_seconds` | `source` | seconds since this week started (Monday 00:00 Spanish time) with no list found; 0 once found |
| `radares_weekly_list_published_timestamp_seconds` | `source` | the list's publication day, midnight UTC |
| `radares_weekly_list_streets` | `source` | streets announced in this week's list |
| `radares_weekly_list_streets_skipped` | `source` | announced streets that could not be placed on the map |
| `radares_street_skipped` | `source`, `street`, `place` | 1 for each of those streets, so the alert can name them |

Timestamps are Unix seconds. A metric with no value yet is left out, such as the last success before
the first run ends.
The weekly-list metrics come from the last collect, even if Home Assistant was down in that run. When
a weekly source fails, its last good list counts only if it is this week's; a list of an earlier week
reports this week's as not found. `radares_street_skipped` has one series per skipped street this week, usually none or
one.

## Alert rules

```yaml
groups:
  - name: radares-anunciados-ha
    rules:
      - alert: RadaresRunsFailing
        expr: radares_consecutive_failed_runs >= 3
        labels:
          severity: warning
        annotations:
          summary: "radares-anunciados-ha failed {{ $value }} runs in a row"
          description: >-
            Home Assistant keeps the previous zones, so new radars are not loaded.
            The container log says why.

      - alert: RadaresNoRecentSuccess
        expr: time() - radares_last_success_timestamp_seconds > 3 * radares_interval_seconds
        labels:
          severity: warning
        annotations:
          summary: "radares-anunciados-ha has not synced for {{ $value | humanizeDuration }}"

      - alert: RadaresSourceDown
        # Overpass answers 504 for an hour now and then; three hours is a real outage.
        expr: radares_source_up == 0
        for: 3h
        labels:
          severity: warning
        annotations:
          summary: "Source {{ $labels.source }} has not answered for 3 hours"
          description: >-
            Its last good result keeps its zones, so new radars from it are not loaded.
            The container log says why.

      - alert: RadaresWeeklyListMissing
        # Monday is publication day; a whole day into the week without a list is a problem.
        expr: radares_weekly_list_missing_seconds > 86400
        labels:
          severity: warning
        annotations:
          summary: "No {{ $labels.source }} radar list found for this week"
          description: >-
            The newspaper may have changed its page or not published the list yet.
            This week's mobile radars give no warning.

      - alert: RadaresStreetSkipped
        expr: radares_street_skipped == 1
        labels:
          severity: info
        annotations:
          summary: "No warning on {{ $labels.street }} ({{ $labels.place }})"
          description: >-
            The {{ $labels.source }} list announces a radar on a street that could not be
            placed on the map, so it has no zone.
```

`RadaresRunsFailing` fires after 3 failed runs, which means Home Assistant failed: a source alone never
fails a run. `RadaresSourceDown` covers the sources, also one that never answered (it has no data
age and no weekly-list series yet). `RadaresNoRecentSuccess` matches `/healthz` and
also catches a run loop that hangs. Every rule above needs a scrape to fire, so add an `up == 0` rule
for the scrape target too. Without it a container that died goes unnoticed.

`RadaresWeeklyListMissing` needs no calendar in Prometheus: the service counts from the start of its own
week, which starts at Monday 00:00 Spanish time whatever the container's `TZ` says.

A skipped street is also named in the "Radares actualizados" notification, so the driver knows it has
no warning without any of this set up. The phones get it after each zone change, and also when the set
of skipped streets changes on its own. The same set does not notify again every hour; a restart of the
container may send it once more. To fix one for good,
add the missing name to [OpenStreetMap](https://www.openstreetmap.org/) or
[open an issue](https://github.com/GeiserX/radares-anunciados-ha/issues) with the street and the district.
[How it works](how-it-works.md#from-a-street-name-to-circles) explains how a street is matched.
