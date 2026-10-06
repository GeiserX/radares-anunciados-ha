# AGENTS.md: radares-anunciados-ha

An open feed of the speed radars announced in Spain, merged into one GeoJSON file, plus a Home Assistant
side that turns the radars near you into zones so the Home Assistant companion app alerts you as you
drive near one. Python managed with uv, shipped as a Docker image on Docker Hub and GHCR, CI on GitHub
Actions.

## Data sources

Every radar in the feed keeps its source and that source's license, and the feed credits each source.

| Key | What it gives | License |
|---|---|---|
| `dgt` | DGT fixed and section radars, [NAP](https://nap.dgt.es/dataset/radares-fijos-dgt) DATEX II | Creative Commons Attribution (NAP, no version) |
| `dgt_invive` | DGT mobile-radar stretches, [NAP](https://nap.dgt.es/es/dataset/tramos-invive); lines, zones opt-in (`RADARES_STRETCH_ZONES`) | same |
| `osm` | [`highway=speed_camera`](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dspeed_camera) nodes and enforcement relations; also the speed-limit lookup (`osm_limits.py`) | ODbL 1.0 |
| `osm_notes` | open OSM notes reporting a camera: kind `reported`, unconfirmed, never a zone; off by default, the published feed turns it on | ODbL 1.0 |
| `murcia` | Policía Local weekly list, through the press | facts only, read in the press |
| `sct`, `sct_remolc` | Servei Català de Trànsit fixed, section and trailer radars | Llicència oberta d'ús d'informació – Catalunya |
| `euskadi`, `navarra` | Basque and Navarra government fixed radars (Spanish IP only) | no reuse licence; reused under Ley 37/2007 |
| `donostia`, `donostia_movil` | Donostia fixed radars and its daily mobile-radar streets | the council's own reuse terms |
| `madrid`, `salamanca` | city open data, fixed and section radars | CC BY 4.0; GNU FDL |
| `leon` | León's monthly mobile-radar post, iLeón as a fallback (Spanish IP only) | no reuse licence; reused under Ley 37/2007; iLeón CC BY-NC 4.0, facts only |
| `barcelona_multas`, `madrid_multas` | places where city traffic fines show mobile radars stood, months behind; Madrid placed by its street register | CC BY 4.0 |

Cadence, publishers and the places that publish nothing usable: [`docs/sources.md`](docs/sources.md).
Check a publisher's reuse terms before adding it, and record them in its `Source.licence`. A public
body with no reuse licence gets `PUBLIC_SECTOR_REUSE` (`sources/base.py`) and its legal notice; the
conditions are in [`LICENSE-DATA.md`](LICENSE-DATA.md#public-bodies-with-no-reuse-licence). Where a
source gives the date of its last update, put it in each record's attribution and in
`SourceResult.updated`.

## The legal line

The project warns from published positions only, which is what keeps it legal in Spain.
[RGC art. 18.3](https://www.boe.es/buscar/act.php?id=BOE-A-2003-23514#a18) bans radar jammers and radar
detectors in a vehicle and excludes "los mecanismos de aviso que informan de la posición de los sistemas de
vigilancia del tráfico". Every feature reads published lists and maps; a feature that senses, receives or
interferes with a radar signal is out of scope, whoever asks for it.

## Layout

- `src/radares_anunciados/sources/`: one module per source (key in the table above). Each exposes
  `SOURCE = Source(key, fetch, attribution, licence, spanish_ip_hosts, max_age_s, provinces, official)` and
  is listed once in `sources/__init__.py`. `official=False` marks a crowd map (OSM): `feed.merge` drops
  its camera within 150 m of a radar from an official source. `fetch(Context)` returns a
  `SourceResult` (radars, stretches, weekly lists) and raises on any failure; the registry then reuses
  its last good result. Contract in `sources/base.py`.
- `model.py`: `Radar`, `Stretch`, `SourceResult`, `today_in_spain`; `provinces.py`: INE codes and
  bounding boxes
- `speed.py`: radius by speed limit; `LOOKUPS` is the hook for a limit lookup; `geo.py`: distances,
  street cover, ETRS89 UTM to WGS84
- `streets.py`: street + district from a police list to circle centres (two Overpass queries);
  `streetnames.py`: abbreviations in police lists ("Avda.", "Pº") expanded
- `osm_limits.py`: the speed limit under a radar whose source gives none, from OpenStreetMap
- `feed.py`: merge, dedupe, dormant streets, GeoJSON; `ha.py`: Home Assistant zone sync over the websocket API
- `store.py`: each source's last good result and the announced streets, in the cache folder
- `metrics.py`: `/metrics` and `/healthz` of `radares run`, standard library only ([`docs/alerting.md`](docs/alerting.md))
- [`blueprints/radar_zone_alert.yaml`](blueprints/radar_zone_alert.yaml): the automation that sends the alert
- [`.github/workflows/feed.yml`](.github/workflows/feed.yml): builds `feed.geojson`, `status.json` and the
  map in [`site/`](site/) every 6 hours and publishes them to GitHub Pages, through the steps in
  [`.github/actions/build-feed`](.github/actions/build-feed/action.yml). The data is ODbL
  ([`LICENSE-DATA.md`](LICENSE-DATA.md)); a new source gets a row there in the same change (a test checks).
- [`tests/fixtures/`](tests/fixtures/): real pages and responses, trimmed. Tests never touch the network.
- [`docs/how-it-works.md`](docs/how-it-works.md) explains the design in full.

## Rules that keep it working

- A `reported` point (an OSM note) never becomes a zone (`ha.zoned`) and never drops or replaces
  another radar (`feed.merge`).
- Zones are passive, name starting with "Radar", icon `mdi:camera-timer` (alerts) or `mdi:camera-off`
  (a dormant street, silent). `ha.py` touches no other zone.
- A dormant street changes only its icon, with `zone/update` on the same zone id. Never delete and
  create a zone whose place did not change: the phone would keep the old id.
- Radius never under 100 m: the iOS app splits smaller zones into three regions of its 20.
- At most `RADARES_MAX_ZONES` (1,000) zones. Over it, drop zones in order; never refuse to sync.
- The iOS app loads new zones only in the foreground, and drops a change within 15 s of the last one it
  stored. Every create or delete notifies the phones, and every change is followed 20 s later by a
  1 cm move of one zone, below the 6 decimals a plan compares.
- A failing source never fails the run and never costs its zones. Only Home Assistant fails a run. A
  failed source shows as down in `/metrics` (`net.cached_get` raises on a failed refresh rather than
  hand back an old copy).
- Only a real sync writes the announced-streets history; `sync --dry-run` never does, and `feed` only
  with `--save-history` (the published feed, whose cache no sync shares).
- The day is Spain's (`model.today_in_spain`), never the container's: it runs on UTC, and a per-day
  list starts at Spanish midnight.
- An Overpass answer is cached only once `net.overpass_answer` takes it (`cached_get(validate=...)`):
  Overpass answers 200 with a `remark` when a query runs out of time.
- Overpass by bounding boxes (`provinces.py`), never an area lookup: it answers 504.
- Never send Overpass a name regex over the whole municipality. It answers 504. Look up the districts
  first, then search `around` them.
- An Overpass query that scans a whole city's box asks for `[maxsize:67108864]`. On 2 Oct 2026 four
  queries over Madrid's box answered 504 within 12 s with the default; with 64 MB they answered in 2 to 13 s.
- A place where fines show a mobile radar is `mobile_recurring`, named without counts so its zone stays
  when a new period arrives. `feed.merge` drops it within 150 m of a camera of any other source and it
  never drops a camera; with no limit it is sized for 50 km/h; `ha.select` keeps it after fixed radars
  and before stretch circles. While a selected source marked `cameras=True` has no result at all
  (failed, no last good copy), `feed.merge` holds back the spots in its provinces and logs it.
- La Opinión's street list is `ul.ft-list--primary`. A plain `ft-list` on the same page holds headlines.
- A street or district not found is skipped, never guessed. It is logged, exported as
  `radares_street_skipped` and named in the "Radares actualizados" notification.
- `feed.yml` publishes from a self-hosted runner in Spain (job `publish`, `schedule` and
  `workflow_dispatch` only); a pull request builds on `ubuntu-latest` (job `check`). No other job
  in any workflow targets the self-hosted runner, and none uses `pull_request_target` (a test
  checks both). That keeps ordinary pull requests off the runner, but a pull request runs its own
  copy of the workflows, so one that edits them could target it. What stops that: the repository
  requires approval of every outside contributor's run, and the runner is ephemeral (one job per
  registration) with no Docker socket.
- Never approve a run of a fork's pull request without reading its changes under `.github/`.
- One radar drawn as several circles is named `Radar anunciado …` (police list) or `Radar de tramo …`
  (section). The blueprint alerts once per such name; every other zone alerts on its own, because
  names like OSM's `Radar (límite 50)` repeat across different cameras.

Checks: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

## Where the tracker syncs

This repo is public, so its tracker syncs only to the private remote named by `sync.remote` in `.beads/config.yaml`. The block above says sync uses "your git remote". Here that never means this GitHub repo. Don't add it as a Dolt remote and don't push `refs/dolt/*` to it.

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
