![Vuegraf Logo](https://github.com/jertel/vuegraf/blob/master/vuegraf.png?raw=true "Vuegraf Logo")

# Overview

The [Emporia Vue](https://emporiaenergy.com "Emporia's Homepage") energy monitoring kit allows homeowners to monitor their electrical usage. It monitors the main feed consumption and up to 8 (or 16 in the newer version) individual branch circuits, and feeds that data back to the Emporia API server.

This project, Vuegraf, fetches those metrics from the Emporia Vue API host and stores the metrics into your own InfluxDB. After installation you will be able to:
* View your energy usage across all circuits on a single graph
* Create alerts to notify when certain energy usage thresholds are exceeded

This project is not affiliated with _emporia energy_ company.

# Dependencies

* [Emporia Vue](https://emporiaenergy.com "Emporia Energy") Account - Username and password for the Emporia Vue system are required.
* [Python 3](https://python.org "Python") - With Pip.
* [InfluxDB 2](https://influxdata.com "InfluxDB") - Host, port, org, bucket, and token are all required. [VictoriaMetrics](https://victoriametrics.com "VictoriaMetrics") is supported as an alternative; see [VictoriaMetrics](#victoriametrics) below.

# Influx

## Setup

If you do not yet have a running InfluxDB 2 instance, you will need to set one up. You can do this very quickly by launching an InfluxDB 2 Docker container as follows:

```
mkdir -p /home/myuser/influxdb2
docker run -v /home/myuser/influxdb2:/var/lib/influxdb2 -p 8086:8086 -e INFLUXD_SESSION_LENGTH=432000 --name influxdb influxdb
```

Substitute an appropriate host path for the `/home/myuser/influxdb2` location above. Once running, access the web UI at `http://localhost:8086`. It will prompt you for a username, password, organization name, and bucket name. The rest of this document assumes you have entered the word `vuegraf` for all of these inputs, except for the password; choose your own password that meets the minimum requirements.

Note that the default session timeout for Influx is only 60 minutes, so this command increases the login session to 300 days.

Once logged in, go to the _Load Data -> API Tokens_ screen and generate a new All Access token with the description of _vuegraf_. Copy the generated token for use in the rest of this document, specifically when referenced as `<my-influx-token>`.

## Dashboard

By default, a new InfluxDB instance will not have any dashboards loaded. You will need to import the included Influx JSON template, or create your own dashboard in order to visualize your energy usage. Because this template contains more than just the dashboard itself you will not be able to use the InfluxDB UI to perform the import. You will need to use the instructions included below.

The included template file named `influx_dashboard.json` includes the provided dashboard and accompanying variables to reproduce the visualizations shown below. This dashboard assumes your main/parent device name contains the word `Panel` (specifically cased as shown), such as `House Panel`, or `Right Panel`. If it does not, the Flux queries will need to be adjusted manually to look for your device's name. Note that nested devices should contain the word `Subpanel` (again using that specific upper/lower casing).

![Influx Dashboard Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/influx_dashboard.png?raw=true "Influx Dashboard")

You will need to apply this template file to your running InfluxDB instance. First, copy the `influx_dashboard.json` file into your new InfluxDB container:

```
docker cp <path-to-vuegraf-project>/influx_dashboard.json influxdb:/var/lib/influxdb2/
```

Next, to import the dashboard, run the following command:

```
docker exec influxdb influx apply -f /var/lib/influxdb2/influx_dashboard.json --org vuegraf --force yes -t <my-influx-token>
```

Replace the `<my-influx-token>` with the All Access Token you generated in the Influx _Load Data -> API Tokens_ screen.

You're now ready to proceed with the Vuegraf configuration and startup.

# VictoriaMetrics

As an alternative to InfluxDB, Vuegraf can write directly to [VictoriaMetrics](https://victoriametrics.com "VictoriaMetrics"), a Prometheus-compatible time series database. Add a `victoriaMetrics` section and point `url` at the VictoriaMetrics HTTP API. It may either replace the `influxDb` section or sit alongside it - if both are configured, every data point is written to both, with each receiving only what it is missing. The MQTT output is unaffected and continues to run alongside either:

```json
    "victoriaMetrics": {
        "url": "http://my.victoriametrics.hostname:8428"
    }
```

The same metric name and tags are used as with InfluxDB, so both destinations produce comparable series, such as `energy_usage{account_name="Primary Residence", device_name="Furnace", detailed="False"}`. The `tagName` and `tagValue_*` options described below are set within this section rather than under `influxDb`. Two further optional fields apply only to VictoriaMetrics:

- `metricName` - Overrides the metric name. Defaults to `energy_usage`. When migrating an existing InfluxDB database with `vmctl`, set this to `energy_usage_usage`, since VictoriaMetrics names InfluxDB line protocol data `<measurement>_<field>`.
- `extraLabels` - Object of static labels added to every series, such as `{"db": "vuegraf"}` to match the `db` label VictoriaMetrics adds when ingesting InfluxDB line protocol. Defaults to none.

# Configuration

The configuration allows for the definition of multiple Emporia Vue accounts. This will only be useful to users that need to pull metrics from multiple accounts. This is not needed if you have multiple Vue devices in a single account. Vuegraf will find multiple devices on its own within each account.

The email address and password must match the credentials used when creating the Emporia Vue account in their mobile app.

Important: Ensure that sufficient protection is in place on this configuration file, since it contains the plain-text login credentials into the Emporia Vue account.

A [sample configuration file](https://github.com/jertel/vuegraf/blob/master/vuegraf.json.sample "Sample Vuegraf Configuration File") is provided in this repository, and details are described below.

## Minimal Configuration
The minimum configuration required to start Vuegraf is shown below.

```json
{
    "influxDb": {
        "version": 2,
        "url": "http://my.influxdb.hostname:8086",
        "org": "vuegraf",
        "bucket": "vuegraf",
        "token": "<my-influx-token>"
    },
    "accounts": [
        {
            "name": "Primary Residence",
            "email": "my@email.address",
            "password": "my-emporia-password"
        }
    ]
}
```

## Advanced Configuration

### Timezones
All data is stored in InfluxDB in UTC. To represent day-summary datapoints, vuegraf fetches a day's data at the end of the day in a certain timezone, configured by the configuration field `timezone`.
- if `timezone` is missing or null or its `upper()` is `"TZ"`, then the "default timezone" will be used
  - the "default timezone" depends on the deployment method of the script
    - If you are using Docker, the container has the timezone set to UTC unless the environment `TZ` is set.
    - If you are running the script natively, it depends on your operating system. For example, in Ubuntu the timezone name is the contents of `/etc/timezone`
- for all values of `timezone` other than the ones named above, the string **SHOULD** be a valid timezone name.

The configured timezone is only relevant for collecting day-scoped data: the script fetches Emporia's "day to date" counter values, so if the account's timezone does not match the script one's, the last hours of the day will not be counted. For example, if your account is in the `America/Los_Angeles` timezone while the script runs its default UTC configuration in a Docker container, the daily summaries will miss the last 8 hours of every day.

For a list of timezones as of late 2023, consult the `TZ identifier` column of the table at [this wikipedia page](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).


### Ingesting Historical Data

If desired, it is possible to have Vuegraf import historical data. To do so, run vuegraf.py with the optional `--historydays` parameter with a value between 1 and 720 (configurable).  When this parameter is provided Vuegraf will start and collect all hourly data points up to the specified parameter, or max history available.  It will also collect one day's summary data for each day, storing it with the timestamp 23:59:59 for each day based on the configured timezone. It is possible to control the maximum number of days (default is 720) that the historical data can be collected by adding (or updating) the top-level `maxHistoryDays` configuration value with a numeric value.

```
maxHistoryDays: 720
```


IMPORTANT - If you restart Vuegraf with `--historydays` on the command line (or forget to remove it from the dockerfile) it will import history data _again_. Re-imported points overwrite the existing ones in the database (they share the same timestamp and tags), so this does not create duplicates, but it can be slow and places unnecessary load on the Emporia servers. For best results, only enable `--historydays` on a single run.

> **VictoriaMetrics users: turn on deduplication.** That overwrite is an InfluxDB property. VictoriaMetrics stores a repeated write as an additional sample at the same timestamp, so re-importing history leaves two samples per reading and a query picks between them arbitrarily. The same applies to any reading collected twice — the current hour's average is provisional and is collected again once the hour closes. Run VictoriaMetrics with [`-dedup.minScrapeInterval=1ms`](https://docs.victoriametrics.com/#deduplication), which keeps the last sample written at a given timestamp and restores the overwrite semantics the rest of this document assumes.

For Example:
```
python3 path/to/vuegraf.py vuegraf.json --historydays 365
```

### CSV Cache

Vuegraf can optionally maintain a CSV copy of your data on disk, alongside your database. This is off by default; enable it with the top-level `csvCacheEnabled` configuration value:

```
csvCacheEnabled: true
csvCacheDir: backfill
```

- `csvCacheEnabled` (default value is `false`): write and read the CSV cache.
- `csvCacheDir` (default value is `backfill`): directory the CSV files are written to. A relative path is resolved against the working directory; an absolute path is used as-is. The directory is created if it does not exist.

Only **settled** readings are mirrored. An open period's average is provisional: Emporia keeps refining it until the period closes, and whoever collects it afterwards writes the final figure under the same key. A time series database absorbs that as an overwrite; an append-only CSV would keep both rows. So the current hour and the current day are held back until they close, and the claimed cache coverage stops at the same boundary, which is what lets the settled value be collected on a later run.

Each account has exactly one data file, named after the account with spaces replaced by underscores — the account named `Primary Residence` writes `Primary_Residence.csv`. It is appended to by both collection paths:

- historical backfill, one append per fetched batch, and
- normal operation, one append per hourly and daily average as it is collected, so the CSV stays as current as the database.

Per-minute points are not mirrored: they are orders of magnitude more voluminous and are already durable in the database.

Keeping it to a single file means the CSV is a self-contained, lower-resolution mirror of the database, ready to load directly without stitching files together:

```python
df = pd.read_csv('backfill/Primary_Residence.csv', parse_dates=['timestamp_utc'])
```

Since both paths share the file, a backfill covering a period the running daemon already mirrored would otherwise write those readings twice. Vuegraf avoids this at the source: before fetching, it reads back the keys already present at or after the current coverage end and skips re-appending matching readings. Readings the daemon *missed* — an hour it was down for, say — are still written, so the file does not develop holes.

#### How large the file gets

A row is about 74 bytes, and each series contributes one row per hour plus one per day — 9,125 rows per year, roughly 660 KiB per series per year:

| Install | Series | Rows/year | 1 year | 5 years | 10 years |
| --- | ---: | ---: | ---: | ---: | ---: |
| One Vue, 8 circuits | 11 | 100 K | 7 MiB | 35 MiB | 71 MiB |
| One Vue, 16 circuits | 19 | 173 K | 12 MiB | 61 MiB | 122 MiB |
| Two Vues, 16 circuits each, + 4 smart plugs | 42 | 383 K | 27 MiB | 135 MiB | 270 MiB |
| Three Vues + 10 plugs, with Net Balance series | 80 | 730 K | 51 MiB | 257 MiB | 513 MiB |

Restoring a range from the CSV, and reading back the already-mirrored keys, each scan the file end to end — about 1.5 seconds per 250 MiB, and only during a `--historydays` run, never during ordinary collection, which just appends.

#### Resumable backfills

When the cache is enabled, the history files act as a local cache in front of the Emporia history API. A small JSON manifest per account records which time range has already been collected:

- On a subsequent `--historydays` run, any portion of the requested range already recorded in the cache is restored directly from the CSV files into every configured database, without calling the Emporia API. Only data newer (or otherwise outside) the cached range is fetched from Emporia to "true-up" the database.
- Windows that returned no data (for example, dates before your monitors were installed) are recorded as covered, so they are not re-probed on later runs.
- This makes long backfills resumable: if a run is interrupted, re-running continues from where the cache left off. Because the coverage is tracked as absolute timestamps, it remains correct even if you change the `--historydays` value between runs.

If the database already contains the cached range and you only want to fetch the newest data, pass `--skiprestore` to skip the CSV-to-database restore step. On VictoriaMetrics this is worth doing whenever the restore is redundant, since re-writing a reading adds a sample there rather than replacing one unless deduplication is enabled — see the note under [Configuration](#configuration).

Note that `--historydays` is silently clamped to `maxHistoryDays` (default `720`). If your cache reaches back further than that, raise `maxHistoryDays` too, or the restore stops short without telling you.

#### Enabling the cache

The cache is purely additive, so it can be turned on at any time — but it only mirrors what Vuegraf collects *from that point on*.

- **New installation.** Enable `csvCacheEnabled` before your first run. Every hourly and daily point is then mirrored as it is collected, and the CSV grows into a complete record alongside the database.
- **Existing installation.** Turning the setting on starts an empty CSV; it does not export the history already sitting in the database. To seed the cache from Emporia, run once with `--historydays` covering the range you want. That populates both the CSV and the database, and re-written points overwrite the existing ones rather than duplicating them.
- **Existing CSV, missing manifest.** If the manifest is deleted but the CSV survives, coverage is bootstrapped by scanning the CSV once on the next run. That scan sees only the first and last timestamps, so it cannot detect interior gaps; delete both files to force a complete re-probe.

> **Series names are the cache's identity.** A reading is keyed by its device and channel name, so renaming a channel makes the new name a *different* series: history keeps accumulating under the old name and the new one starts empty, in the CSV and in the database alike. This is longstanding Vuegraf behaviour rather than anything the cache introduces, but the cache makes it durable. Settle your `devices[].channels` names (see [Channel Names](#channel-names)) before collecting history you care about.
>
> The same applies to the *absence* of a name map: with no `channels` entry for a device, Vuegraf falls back to naming circuits `<Device>-<channelNum>`. Adding descriptive names later renames every one of those series at that instant.

> **Nesting a device changes what its parent's series means.** Emporia deducts a nested device's usage from the channel it sits under, and Vuegraf restores it so a reading means the same thing whoever collected it. Nest a plug under a circuit — or move it — and every reading of that parent from then on measures something different from the ones before it. The cache faithfully records both, so the file spans two definitions with a step at the moment you changed it.
>
> Emporia's history API answers as-was: re-fetching an old range returns what was true then, not what today's tree would imply. So a hierarchy change cannot be repaired by re-running `--historydays`, and Vuegraf does not try — reconciling two measurement definitions needs judgement about which one you want, not code. If you change nesting and want one consistent lens over the whole archive, start the cache clean. Otherwise, note the date you changed it.

### Channel Names

To provide more user-friendly names of each Vue device and branch circuit, the following device configuration can be added to the configuration file, within the account block. List each device and circuit in the order that you added them to the Vue mobile app. The channel names do not need to match the names specified in the Vue mobile app but the device names must match. The below example shows two 8-channel Vue devices for a home with two breaker panels.

Be aware that the included dashboard assumes your device name contains the word "Panel". For best results, consider renaming your Vue device to contain that word, otherwise you will need to manually adjust the included dashboards' queries.

```json
            "devices": [
                {
                    "name": "Right Panel",
                    "channels": [
                        "Air Conditioner",
                        "Furnace",
                        "Coffee Maker",
                        "Oven",
                        "Pool Vacuum",
                        "Pool Filter",
                        "Refrigerator",
                        "Office"
                    ]
                },
                {
                    "name": "Left Panel",
                    "channels": [
                        "Dryer",
                        "Washer",
                        "Dishwasher",
                        "Water Heater",
                        "Landscape Features",
                        "Septic Pump",
                        "Deep Freeze",
                        "Sprinkler Pump"        
                    ]
                }
            ]
```

You can also explicity define the channel and circuit names by using a dictionary in the configuration. Circuits that are merged in the Emporia App may be given a channel assignment that is far out of the range of your circuts (ex. 98 or 99). In this case, assigning the channels directly using a dictionary is preferred. 

```json
            "devices": [
                {
                    "name": "Right Panel",
                    "channels": {
                        "2" : "Upstairs bathroom",
                        "3" : "Back bedroom",
                        "13" : "Master Bedroom",
                        "14" : "Basement Family Room",
                        "97" : "1st Floor Heat Pump",
                        "98" : "2nd floor Heat Pump"
                    }
                },
                {
                    "name": "Left Panel",
                    "channels": {
                        "1" : "Kitchen Outlets",
                        "6" : "Washer",
                        "7" : "Garage Lights",
                        "97" : "Water Heater",
                        "98" : "Dryer"
                    }
                }
            ]
```

### Device Hierarchy and Net Balance

Emporia allows devices to be nested: smart plugs under a circuit, and circuits feeding subpanels. A real installation is therefore a tree, but Vuegraf records every device and channel as an independent, flat series, with no notion that one reading is physically a subset of another. That leads to two problems:

- **Double counting.** A subpanel's energy is measured by the parent's feed circuit *and* by the subpanel device (and again by the subpanel's own circuits), so naively summing device series counts the same watts two or three times. Correct totals require hand-maintained exclusion lists in your queries.
- **No true balance.** Emporia's per-device `Balance` channel nets out only that one device's own circuits. It does not subtract a separately measured subpanel or plug, so no series answers "how much did this panel use that isn't already itemized somewhere below it?"

This feature is **off by default**. Describe your panel tree once in the configuration and Vuegraf will emit a correct per-level balance for both live and historical data. It is purely additive: raw Emporia readings, including the native `Balance` channel, are written unchanged.

#### Declaring the tree

Add an optional `parent` to any device in the account's `devices` list. A `parent` may name another device *or* a channel/circuit name, since a single circuit can feed several plugs or a subpanel:

```json
            "devices": [
                { "name": "Main Panel", "channels": ["Heat Pump", "Garage Feed", "Media Circuit"] },

                { "name": "Garage Subpanel", "parent": "Garage Feed", "channels": ["Garage Lights", "Freezer"] },

                { "name": "TV Console",   "parent": "Media Circuit" },
                { "name": "Network Rack", "parent": "Media Circuit" },

                { "name": "Sump Pump", "parent": "Main Panel" }
            ]
```

That configuration describes this tree. `Main Panel` and `Garage Subpanel` are Vue devices; `Heat Pump`, `Garage Feed`, and `Media Circuit` are circuits on the main panel; the last three are smart plugs:

```
Main Panel                        (device, mains)
├── Heat Pump                     (circuit, leaf)
├── Garage Feed                   (circuit, feeds the subpanel)
│   └── Garage Subpanel           (device, mains)
│       ├── Garage Lights         (circuit, leaf)
│       └── Freezer               (circuit, leaf)
├── Media Circuit                 (circuit, feeds two plugs)
│   ├── TV Console                (plug, leaf)
│   └── Network Rack              (plug, leaf)
└── Sump Pump                     (plug on the panel's unmetered remainder)
```

Two things in that example are worth calling out, because they are the cases a flat series list gets wrong:

- **`Garage Subpanel` hangs off `Garage Feed`, a circuit, not off `Main Panel` directly.** The subpanel's energy is physically measured twice — once by the feed circuit in the main panel, once by the subpanel's own mains. Naming the circuit as the parent is what tells Vuegraf those two readings are the same watts.
- **`Sump Pump` hangs off `Main Panel` itself.** It is a plug on some circuit the panel does not meter individually, so its parent is the panel, and it comes out of the panel's own remainder rather than any circuit's.

Edges come from two places and are merged into one tree:

- **Explicit** `parent` entries from the configuration. This is the only way to express a link *across* Vue devices (subpanel to panel), which Emporia does not report.
- **Implicit** edges from the nested devices Emporia reports for plugs it natively nests inside a single device.

Where both exist and disagree, the explicit configuration wins and a warning is logged once.

If your account's tree comes entirely from Emporia's own nesting and needs no `parent` entries, turn the feature on for that account with `"hierarchyEnabled": true` alongside `devices`.

#### What gets written

For every node that has children, Vuegraf writes a new channel series named `<node> Net Balance`:

```
Net Balance(node) = total(node) - sum(total(child) for each direct child)
```

A panel or subpanel's total is its mains (`1,2,3`) reading; a circuit's total is its channel reading. A circuit that feeds children therefore gets its own balance, which is the portion of that circuit not itemized by the plug or subpanel below it. Leaf nodes have nothing beneath them, so they get no balance series.

Summing every `Net Balance` series plus the totals of every leaf reconstructs your whole-home total with nothing counted twice — no query-side exclusion lists.

##### A worked example

Take the tree above, and suppose one hour is collected with these readings (average watts):

| Series | Reading |
|---|---|
| `Main Panel` (mains) | 4000 |
| `Heat Pump` | 1500 |
| `Garage Feed` | 900 |
| `Garage Subpanel` (mains) | 800 |
| `Garage Lights` | 100 |
| `Freezer` | 250 |
| `Media Circuit` | 400 |
| `TV Console` | 150 |
| `Network Rack` | 200 |
| `Sump Pump` | 300 |

Vuegraf adds one `Net Balance` series per node that has children:

| New series | Arithmetic | Value |
|---|---|---|
| `Main Panel Net Balance` | 4000 − (1500 + 900 + 400 + 300) | 900 |
| `Garage Feed Net Balance` | 900 − 800 | 100 |
| `Garage Subpanel Net Balance` | 800 − (100 + 250) | 450 |
| `Media Circuit Net Balance` | 400 − (150 + 200) | 50 |

Read them as "energy at this level that nothing below it accounts for": 900 W of main-panel load on circuits with no CT at all, 100 W lost in or tapped off the garage feed before the subpanel, 450 W of unmetered garage circuits, and 50 W on the media circuit that is not the TV or the rack.

The four balances plus the six leaves total exactly 4000 W — the main panel reading, reconstructed with nothing double counted:

```
900 + 100 + 450 + 50                          (balances)
  + 1500 + 100 + 250 + 150 + 200 + 300        (leaves)
= 4000
```

Naively summing the ten raw series instead gives 8600 W, because the subpanel, its feed, and the plugs are each counted at two or three levels.

##### What the raw series already contain

Worth knowing before writing your own queries, because Emporia's API and its mobile app do not present a circuit the same way:

- **A circuit's reading already includes any plug nested under it.** The API does not deduct there, so a plug's energy is counted twice if you add the plug's own series to its parent circuit's. Note that the Emporia mobile app *does* deduct at the circuit, so the same circuit reads higher through the API than it does in the app.
- **A device's mains is deducted**, and Vuegraf adds the nested device back so the value means the whole panel. That asymmetry is Emporia's, not Vuegraf's; the add-back exists so a mains reading and a circuit reading can be interpreted the same way.

Vuegraf writes the raw series through untouched, so **any total you build by adding series yourself has to exclude a nested device once it is already inside its parent**. That is exactly the bookkeeping `Net Balance` removes: sum the balances plus the leaves and every reading is counted once, whatever is nested where.

If your service has more than one top-level panel (dual mains), Vuegraf synthesizes a virtual root named `<account> Panel` and writes the sum of those panels under that name, giving you a single correct whole-account total. Emporia has no way to express a root above independent mains.

#### Scope: hourly and daily only

Balance is a subtraction across nodes, so it is only meaningful when a node and all of its children are sampled at the same instant. Hourly and daily points are collected once per period rollover, for the just-closed period, in a single API call — so every device lands one point at the same timestamp for a period that is already complete. Per-minute points are produced by a per-channel path that does not line up within a cycle, so **no minute-resolution Net Balance is emitted**.

Both the live rollover and the `--historydays` backfill produce these aligned groups, so both are covered. With the CSV cache enabled, the balances flow into the CSV automatically, since they are appended to the same list that gets written.

#### Settings

```
hierarchyBalanceEpsilonWatts: 5.0
hierarchyNegativeBalanceAbort: false
hierarchyAggregateSettleSecs: 120
```

- `hierarchyBalanceEpsilonWatts` (default `5.0`): a negative balance smaller than this is treated as rounding noise and clamped silently.
- `hierarchyNegativeBalanceAbort` (default `false`): a balance more negative than the epsilon means a child is measuring more than its parent, which usually means the tree is wrong. By default this logs a warning (at most once per node per hour) and clamps the emitted value to zero. Set this to `true` to raise instead, failing the collection cycle loudly.
- `hierarchyAggregateSettleSecs` (default `120`, capped at 1800): how long to wait after a period closes before collecting it, giving Emporia time to finalize its averages. Only applied when a hierarchy is active; the deferred period is picked up on a later cycle.

A `parent` that does not resolve to a real device or channel, a cycle in the tree, or a name that refers to both a device and a channel is reported at startup with the offending node named, rather than silently producing wrong balances.

#### Enabling the hierarchy

Nothing about existing data changes when you turn this on: the raw Emporia series keep their names, their values, and their history. The feature only *adds* `Net Balance` series, so existing dashboards and queries keep working untouched.

- **New installation.** Name your channels first (see [Channel Names](#channel-names)), then declare the tree. Balances are emitted from the first hourly rollover onward.
- **Existing installation.** Add the `parent` entries and restart. Vuegraf computes balances only for points it collects from then on, so the new series begin at that moment and existing points get no balance retroactively. To fill them in for data you already have, re-run once with `--historydays`; re-written points overwrite the existing ones in the database rather than duplicating them.
- **Upgrading Vuegraf without configuring a tree.** The feature stays off, and collection behaves exactly as before. It activates only when an account declares a `parent` or sets `"hierarchyEnabled": true`.

Two things to check before declaring a tree on an established installation:

- **Node names are series names.** A `parent` must name a device or channel exactly as Vuegraf names its series — which comes from `devices[].channels`, not from the Emporia app. If a device has no `channels` map, its circuits are named `<Device>-<channelNum>`, and that is the name a `parent` must use. Adding descriptive names later renames those series and forks their history, so settle the names first.
- **Every node name must be unique.** Vuegraf refuses to start if one name refers to both a device and a channel — a common case is a feed circuit labelled with the name of the subpanel it feeds ("Garage Subpanel" as both a circuit and the Vue device). Rename one of them; in the example above the circuit is `Garage Feed` for exactly this reason.

If a negative balance appears after enabling, the tree is usually claiming a child that is not actually below its parent. `hierarchyNegativeBalanceAbort` turns those warnings into hard failures while you are validating a new tree.

### Negative Readings

```
clampNegativeUsage: false
```

- `clampNegativeUsage` (default `false`): when enabled, any reading below zero is recorded
  as zero, and a warning naming the device and channel is logged at most once per series
  per hour.

Emporia's derived `Balance` channel is the device total minus its circuits, so it goes
negative whenever the parts read higher than the whole — a single mis-scaled channel (a
120V circuit configured as 240V, say) or ordinary CT tolerance is enough. That is a
measurement fault rather than energy flowing backwards, and left alone it distorts every
sum and graph built on the series. This is the same treatment the hierarchy already gives
its own `Net Balance`.

**Leave this off if anything in your system can produce power.** On a solar or battery
install a negative reading is legitimate — it means export — and clamping would discard
real data. Enable it only where a negative value can only be an error.

Note that clamping hides the symptom, not the cause: the throttled warning is there so a
standing fault stays visible. A channel that keeps needing to be clamped usually has the
wrong circuit type or multiplier configured in the Vue app.

### Station Names

If you intend to run multiple Vue systems under the same account, where the channel names duplicate or look similar across those Vue systems then you may want to consider enabling the `addStationField` config parameter. This will include an additional field named 'station_name' in the InfluxDB event record, to help distinguish channel names across those Vue systems or 'stations'.

Note that enabling this at a later time will cause issues due to queries matching multiple records. Therefore if you are installing Vuegraf for the first time and think this could be useful then enable it at the start.

### MQTT

In addition to publishing to Influx, you can send pubsub messages to a MQTT server such as [Mosquitto](https://mosquitto.org/). MQTT only sends the latest timestamped value per channel in each batch (so it will not flood the topic with historical messages when `vuegraf` starts). The minimal config  would just add the host:

```json
{
    "influxDb": {
        ...
    },
    "accounts": [
        {
            ...
        }
    ],
    "mqtt": {
      "host": "my.mqtt.host"
    }
}
```

There are additional keys for authentication and topic customization:

```json
    "mqtt": {
      "host": "my.mqtt.host",
      "port": 8999,
      "username": "my_mqtt_user",
      "password": "my_mqtt_pw",
      "topic": "custom/vue/topic/for/energy_usage"
    }
```

By default, messages will be sent to the `vuegraf/energy_usage` topic. An example showing the structure:

```json
{"account": "Vue Account", "device_name": "Left Panel-7", "usage_watts": 275.02, "epoch_s": 1759441380, "detailed": "False"}
```

# Running
Vuegraf can be run either as a container (recommended), or as a host process.

## Container (recommended)

A Docker container is provided at [hub.docker.com](https://hub.docker.com/r/jertel/vuegraf). Refer to the command below to launch Vuegraf as a container. This assumes you have created a folder called `/home/myuser/vuegraf` and placed the vuegraf.json file inside of it.

Normal run with docker
```sh
docker run --name vuegraf -d -v /home/myuser/vuegraf:/opt/vuegraf/conf jertel/vuegraf
```

Recreate database and load 25 days of history
```sh
docker run --name vuegraf -it -v /home/myuser/vuegraf:/opt/vuegraf/conf jertel/vuegraf --resetdatabase --historydays=24 /opt/vuegraf/conf/vuegraf.json
```

## Host Process

Ensure Python 3 and Pip are both installed. Install the Vuegraf module:

```sh
pip install vuegraf
```
or, on some Linux installations:

```sh
pip3 install vuegraf
```


Then run the program, specifying the JSON configuration file path as the only argument:

```sh
vuegraf vuegraf.json
```
or, on some Linux installations:
```sh
vuegraf vuegraf.json
```

Optional Command Line Parameters
```
usage: vuegraf.py [-h] [--version] [-v] [-q] [--historydays HISTORYDAYS] [--resetdatabase] configFilename

Retrieves data from cloud servers and inserts it into an InfluxDB database.

positional arguments:
  configFilename        JSON config file

options:
  -h, --help            show this help message and exit
  --version             Display version number
  -v, --verbose         Verbose output - summaries
  --historydays HISTORYDAYS
                        Starts executing by pulling history of Hours and Day data for specified number of days.
                        example: --load-history-day 60
  --resetdatabase       Drop database and create a new one
  --skiprestore         During history backfill, skip restoring the cached CSV range into the database
                        (assume it is already present) and only fetch/true-up the uncovered range
```

## Alerts

The included dashboard template contains two alerts which will trigger when either a power outage occurs, or a loss of Vuegraf data. There are various reasons why alerts can be helpful. See the below screenshots which help illustrate how a fully functioning alert and notification rule might look. Note that the included alerts do not send out notifications. To enable outbound notifications, such as to Matrix or Slack, you can create a Notification Endpoint and Notification Rule.

This alert was edited via the text (Flux) interface since the alert edit UI does not yet accommodate advanced alerting inputs.

Side note: The logo at the top of this documentation satisfies Slack's icon requirements. Consider using it to help quickly distinguish between other alerts.

![Influx Alert Edit](https://github.com/jertel/vuegraf/blob/master/screenshots/alert_edit.png?raw=true "Influx Alert")

This notification rule provides an example of how you can have several alerts change the status to crit, but only a single notification rule is required to transmit notifications to external endpoints (such as email or Slack).

![Influx Notification Rule](https://github.com/jertel/vuegraf/blob/master/screenshots/notification_rule.png?raw=true "Influx Notification Rule")

To send alerts to a Matrix chat room hosted on a Synapse server, use a [Hookshot bot](https://github.com/matrix-org/matrix-hookshot) with appropriate URL path routing (via a reverse proxy), and in InfluxDB, specify the Alert Endpoint as follows:
- Destination: HTTP
- HTTP Method: POST
- Auth Method: None
- URL: https://my-matrix-host.net/webhook/1234abcd-4321-1234-abcd-abcdef123456

In the above example, /webhook/ routes to the Hookshot server. Then edit the Alert Notification Rule, and change the `body` variable assignment as follows:
```
body = {text: "🔴 ${r._notification_rule_name} -> ${r._check_name}"}
```

# Additional Topics

## Per-second and per-hour Data Details

By default, Vuegraf will poll every minute to collect the energy usage value over the past 60 seconds. This results in a single value being captured per minute per channel, or 60 values per hour per channel. If you also would like to also fetch per-second and/or per-hour values, you can enable the detailed collection, which is polled once per hour, and backfilled over the previous 3600 seconds. This API call is very expensive on the Emporia servers, so it should not be polled more frequently than once per hour. To enable this detailed data, add (or update) the top-level `detailedDataEnabled` configuration value with a value of `true`.

```
detailedDataEnabled: true
```

If `detailedDataEnabled` is set to `true`, the following two configuration fields become relevant. Notice that they are _not_ mutually exclusive and are actually both set to `true` unless overridden:
- `detailedDataSecondsEnabled` (default value is `true`): fetch and store per-second data every hour
- `detailedDataHoursEnabled` (default value is `true`): fetch and store per-hour data every hour

For every datapoint a tag is stored in InfluxDB for the type of measurement

- `detailed = True` represents backfilled per-second data that is optionally queried from Emporia once every hour.
- `detailed = False` represents the per-minute average data that is collected every minute.
- `detailed = Hour` represents the data summarized in hours
- `detailed = Day` represents a single data point to summarize the entire day

When building graphs that show a sum of the energy usage, be sure to only include the correct detail tag, otherwise your summed values will be higher than expected. Detailed data will take more time for the graphs to query due to the extra data involved. If you want to have a chart that shows daily data over a long period or even a full year, use the `detailed = Day` tag.
If you are running this on a small server, you might want to look at setting a RETENTION POLICY on your InfluxDB bucket to remove minute or second data over time. For example, it will reduce storage needs if you retain only 30 days of per-_second_ data. 

The name of the "detailed" tag as well as the associated tag values (True, False, Hour, Day) can be changed via the configuration file by providing the appropriate value within the InfluxDb section:

- `tagName` will be name of the tag within the database. Default value is `detailed`
- `tagValue_second` will be the value set for the tagName for the per-second data.  Default value is `True`
- `tagValue_minute` will be the value set for the tagName for the per-minute data.  Default value is `False`
- `tagValue_hour` will be the value set for the tagName for the per-hour data.  Default value is `Hour`
- `tagValue_day` will be the value set for the tagName for the per-day data.  Default value is `Day`

```json
{
    "influxDb": {
        "version": 2,
        "url": "http://my.influxdb.hostname:8086",
        "org": "vuegraf",
        "bucket": "vuegraf",
        "token": "<my-influx-token>",
        "tagName": "granularity",
        "tagValue_second": "second",
        "tagValue_minute": "minute",
        "tagValue_hour": "hour",
        "tagValue_day": "day"
    },
    "accounts": [
        {
            "name": "Primary Residence",
            "email": "my@email.address",
            "password": "my-emporia-password"
        }
    ]
}
```


## Vue Utility Connect Energy Monitor

As reported in [discussion #104](https://github.com/jertel/vuegraf/discussions/104), the Utility Connect device is supported without any custom changes.

## Smart Plugs

To include an Emporia smart plug in the configuration, add each plug as it's own device, without channels. Again, the name of the Smart Plug device must exactly match the name you gave the device in the Vue app during initial registration.

```json
            devices: [
                {
                    "name": "Main Panel",
                    "channels": [
                        "Air Conditioner",
                        "Furnace",
                        "Coffee Maker",
                        "Oven",
                        "Dishwasher",
                        "Tesla Charger",
                        "Refrigerator",
                        "Office"
                    ]
                },
                {
                    "name": "Projector Plug"
                },
                {
                    "name": "3D-Printer Plug"
                }
            ]
```

### Plugs assigned to a circuit

The Vue app lets you attach a smart plug (or a second monitor) to the circuit that feeds
it, so its consumption is shown as part of that circuit. When you do, Emporia's
device-list endpoint reports the parent circuit **net of** the attached device, while the
history endpoint used by `--historydays` reports the circuit whole. Vuegraf restores the
circuit's own total in both cases, so a circuit series always means "everything flowing
through this circuit" and the attached device's series is the itemization of the
sub-metered part.

That keeps a backfilled hour and a live hour comparable — without it, the same hour holds
different values depending on which path collected it — and it is what lets the
[device hierarchy](#device-hierarchy-and-net-balance) subtract a child from its parent
without counting it twice. Device totals (the `1,2,3` mains channel) are never adjusted;
Emporia does not deduct attached devices from those.

## Docker Compose

For those that want to run Vuegraf using Docker Compose, the following files have been included: `docker-compose.yaml.template` and `docker-compose-run.sh`. Copy the`docker-compose.yaml.template` file to a new file called `docker-compose.yaml`. In the newly copied file, `vuegraf.volumes` values will need to be changed to the same directory you have created your vuegraf.json file. Additionally, adjust the persistent host storage path for the InfluxDB data volume.

Finally run the `docker-compose-run.sh` script to start up the multi-container application. 

```sh
./docker-compose-run.sh
```

## Upgrading from InfluxDB v1

Early Vuegraf users still on InfluxDB v1 can upgrade to InfluxDB 2. To do so, stop the Influx v1 container (again, assuming you're using Docker). Then run the following command to install InfluxDB 2 and automatically upgrade your data.

```
docker run --rm --pull always -p 8086:8086 \
  -v /home/myuser/influxdb:/var/lib/influxdb \
  -v /home/myuser/influxdb2:/var/lib/influxdb2 \
  -e DOCKER_INFLUXDB_INIT_MODE=upgrade \
  -e DOCKER_INFLUXDB_INIT_USERNAME=vuegraf \
  -e DOCKER_INFLUXDB_INIT_PASSWORD=vuegraf \
  -e DOCKER_INFLUXDB_INIT_ORG=vuegraf \
  -e DOCKER_INFLUXDB_INIT_BUCKET=vuegraf \
  -e DOCKER_INFLUXDB_INIT_RETENTION=1y \
  influxdb
```

Adjust the host paths above as necessary, to match the old and new influxdb directories. The upgrade should complete relatively quickly. For reference, a 7GB database, spanning several months, upgrades in about 15 seconds on SSD storage.

Monitor the console output and once the upgrade completes and the Influx server finishes starting, shut it down (CTRL+C) and then restart the Influx DB using the command referenced earlier in this document.

Login to the new Influx DB 2 UI from your web browser, using the _vuegraf / vuegraf_ credentials. Go into the _Load Data -> Buckets_ screen and rename the `vue/autogen` bucket to `vuegraf` via the Settings button.

Finally, apply the dashboard template as instructed earlier in this document.

## Productionalizing the Server

There are additional steps necessary for making this configuration fault tolerant. Consider implementing the following:

- Configuring the container to always restart (such as after a reboot or a crash)
- Backing up the InfluxDB on a frequent basis
- Configure logging for rollover management, such as by file size or date
- Configure OS alerts to an admin when detecting crashes of critical software, such as InfluxDB
- Checking for low disk space on the host and alerting an admin
- Setting up calendar reminders for host OS updates and associated kernel reboots
- Updating Vuegraf and Influx on a schedule
- Much more!

These topics are out of scope of this project, but are intended to help new system administrators understand different areas that need to be considered for ensuring disaster recovery and prevention of vulnerabilities.

# Developer Setup

Set up a virtual environment with Python >= 3.12. Then to run in your virtual environment and pick up local changes:

```sh
python3 -m pip install -r src/requirements-dev.txt -r src/requirements.txt
python3 -m pip install -e .  # install the package from setup.py
cp vuegraf.json.sample vuegraf.json  # and edit
python3 -m vuegraf.vuegraf vuegraf.json
```

After making changes, you can run `pytest` from the root project directory to run all unit tests, or `make test-docker` for a containerized test setup. Also check test coverage and flake8 (commands in [`tox.ini`](src/tox.ini) are used by `test-docker`).

# License

Vuegraf is distributed under the MIT license.

See [LICENSE](https://github.com/jertel/vuegraf/blob/master/LICENSE) for more information.
