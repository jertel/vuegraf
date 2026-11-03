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


IMPORTANT - If you restart Vuegraf with `--historydays` on the command line (or forget to remove it from the dockerfile) it will import history data _again_. This will likely cause confusion with your data since you will now have duplicate/overlapping data. For best results, only enable `--historydays` on a single run.

For Example:
```
python3 path/to/vuegraf.py vuegraf.json --historydays 365
```

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

## Nested Devices and Net Balance

Emporia lets devices be nested: a smart plug under a circuit or directly under a panel, or a subpanel fed from a circuit on another panel. An installation is therefore a tree, but Vuegraf records every device and channel as its own series. Optionally, Vuegraf can also write a **Net Balance** for every level of that tree: the part of a panel's or circuit's usage that nothing beneath it accounts for. This is off by default, and turning it on never changes any existing series; it only adds new ones.

### How Emporia reports nested devices

Vuegraf gets its data from Emporia in two ways, and they handle nesting differently. That decides what the existing series mean.

**Live collection** (every minute, and the hourly and daily rollups) asks for every device on the account in a single request.

- A device nested in the Emporia app comes back inside the channel it is nested under, not as a separate device.
- A circuit is reported *net*: minus the live readings of the devices nested beneath it, floored at zero. Emporia only subtracts nested devices that are part of the same request, which is why Vuegraf asks for every device at once. A panel requested on its own gets its circuits back raw.
- A panel's mains is never reduced, even when a device is nested directly on it.
- `Balance` is the mains minus the *raw* circuits and minus any device nested directly on the mains. It is not floored. So a panel's circuits, the devices nested under those circuits, the devices nested on its mains, and its `Balance` add up to the mains. The exception is a circuit that reads less than what is nested beneath it. Emporia floors that circuit at zero, but `Balance` still subtracts its lower raw reading, so the pieces come to more than the mains by the difference (see [When the numbers do not fit](#when-the-numbers-do-not-fit)).
- Some responses also carry channels that are not circuits, such as `TotalUsage`, `MainsFromGrid` and `MainsToGrid`. Vuegraf writes them as returned, as series named like `Main Panel-TotalUsage`. They overlap the mains, so leave them out of any sum.
- For a panel with no mains sensor, Emporia makes up the mains as the sum of its circuits and returns no `Balance`.

**History collection** (`--historydays`) asks for each channel separately.

- Every circuit is reported *raw*, including anything nested beneath it. This is also what the app's history view shows, which is why the app offers `Balance` only in its real-time view.
- There is no `Balance`, and none of the extra channels above.
- A panel with no mains sensor has no mains reading at all.

Otherwise the two paths agree. Hourly readings match exactly; daily and minute readings can differ very slightly.

So for a device nested in the app, the live series already avoid double counting. What Emporia cannot know about are links it has not been told: a subpanel wired from a circuit on a different Vue, or a plug the app does not nest. Neither of Emporia's two views gives a history backfill a remainder for each level.

### Caution: one circuit series, two meanings

Both collection paths write a circuit to the same series: for example `Main Panel / Office`, tagged `Hour`. For a circuit with a device nested beneath it in the app, the two paths store different values for the same hour. This is Emporia's behaviour and is unchanged by this feature, with or without the hierarchy enabled.

Take an hour where the Office circuit's CT measures 113 Wh, and a Desk plug nested under Office in the app measures 80 Wh of that. The panel's mains reads 1,000 Wh, and its other circuits 700 Wh.

| Series | Hour collected live | Same hour from `--historydays` |
|---|---|---|
| `Main Panel` (mains) | 1,000 | 1,000 |
| `Office` | **33** (Desk already subtracted) | **113** (raw, as the app's history view shows) |
| `Desk` | 80 | 80 |
| other circuits | 700 | 700 |
| `Main Panel-Balance` | 187 | *not written* |
| circuits + plugs + `Balance` | 1,000 | 893, with no `Balance` to add |
| circuits + plugs + (mains − circuits) | 1,000 | **1,080**, more than the mains |

In the backfilled hour, the Desk's 80 Wh is counted twice: once inside `Office` and again as `Desk`. Any query that adds circuits and plugs, or that makes up for the missing `Balance` as the mains minus the circuits, comes out higher than the mains.

Because both paths write the same series, what an hour holds depends on which path wrote it last. Hours collected live hold 33, hours only ever backfilled hold 113, and running `--historydays` over hours already collected live replaces their 33 with 113. A graph of `Office` can therefore step between the two at the edge of a backfill. Only circuits with a device nested beneath them are affected. Panel mains, plugs and circuits with nothing nested beneath them read the same on both paths. One exception: a panel with no mains sensor has a mains series only for hours collected live.

The Net Balance series below don't have this problem: they are computed the same way from either path. Here `Office Net Balance` is 33 and `Main Panel Net Balance` is 187 for both the live and the backfilled hour, and 33 + 80 + 700 + 187 = 1,000.

### Turning it on

Add `"hierarchyEnabled": true` to an account to use the nesting from the Emporia app as it is. Add a `parent` to a device to describe a link the app does not have. A parent may name another device, meaning the device hangs off that panel's mains, or a circuit name, meaning it is fed from that circuit. Declaring any `parent` also turns the feature on.

```json
"hierarchyEnabled": true,
"devices": [
    { "name": "Main Panel", "channels": { "1": "Garage Feed", "2": "Kitchen" } },
    { "name": "Garage Subpanel", "parent": "Garage Feed" },
    { "name": "Aquarium Plug", "parent": "Main Panel" }
]
```

A `parent` that repeats the app's nesting is fine. One that contradicts it is refused at startup: Emporia reduces the parent according to its own nesting whatever the config says, so no balance could come out right. Change the nesting in the app, or remove the `parent`. Each device may appear only once in `devices`, and a name used as a `parent` must be unique across the account's devices and circuits. Readings are matched by name, so with the hierarchy on, two readings may not share a series. Two devices with the same name, or two circuits of one panel with the same name, are refused at startup; rename one in the Emporia app or in `channels`. The same circuit name on different panels is fine.

Net Balances are derived from the hourly and daily averages, so for an account that opts in, those are collected even when `detailedDataEnabled` is off. `detailedDataHoursEnabled` and `detailedDataDaysEnabled` still turn either one off. If both are off, only `--historydays` writes Net Balances, and a warning says so at startup.

For a split-feed service, or any grouping with no meter of its own, declare a **virtual** device and place the real panels under it. Its total is written as a series named after it: the sum of the parts that reported. If a part reports nothing for a period, it is left out of that period's total, so the total dips rather than disappearing. Vuegraf logs a warning when a part stops reporting and a note when it comes back.

```json
{ "name": "Service", "virtual": true },
{ "name": "Left Panel", "parent": "Service" },
{ "name": "Right Panel", "parent": "Service" }
```

When an account has more than one top-level panel, Vuegraf also writes the whole account's total as `<account> Panel`, the sum of those panels. This could be the two feeds of a split service, or a declared virtual device plus a detached building. A smart plug that is not placed anywhere is left out, since there is no way to know which circuit it draws from. A panel that reports nothing is left out of a period's total as described above, and counted again once it reports. A panel that is actually fed from another panel must be given a `parent`, or it is counted twice in this total.

### What is written

For each hourly and daily reading, live or backfilled:

- `<panel> Net Balance`, for every panel: the part of its mains no circuit or nested device accounts for. With nothing configured, this equals Emporia's `Balance`, and it is also filled in for backfilled history, where Emporia provides no `Balance`.
- `<circuit> Net Balance`, for every circuit with something nested under it: the part of the circuit nothing nested under it accounts for. For a device nested in the app, this equals Emporia's live reading of the circuit. The app's history view shows the circuit raw, which is this plus what is nested beneath it.
- `<virtual>`, the total of a virtual device.

Minute and second data are not included.

**To total a panel, or the whole service,** add up its Net Balances and every reading with nothing beneath it: the plugs, the circuits with nothing nested, and the panels' own Net Balances. Every watt is then counted exactly once. Don't also add Emporia's `Balance`: it is already inside the panel's Net Balance. Leave out `TotalUsage`, `MainsFromGrid` and `MainsToGrid` too, since they overlap the mains.

### When the numbers do not fit

If a node reads less than what is beneath it, a meter or the tree is wrong, and the pieces will not add up to the total. Vuegraf logs a warning naming the node and the shortfall, at most once an hour per node.

- A circuit's Net Balance is floored at zero, as Emporia floors the circuit itself. In live data Emporia hides the shortfall, so Vuegraf measures it from the gap it leaves in the panel's `Balance`. This usually means a missing or misconfigured CT on that circuit.
- A panel's Net Balance is not floored, like Emporia's `Balance`. If a device the config places on the mains causes the shortfall, the warning says so: that device is probably fed from one of the panel's circuits instead.
- A node that reported nothing gets no Net Balance for that period. Whatever did report beneath it still comes off its parent, so nothing is counted twice. For example, a lamp on a circuit whose CT reported nothing is taken out of the panel's remainder, and a subpanel whose mains reported nothing is taken off its feed by its circuits' readings. A device with nothing reporting at all counts as zero, meaning none of its parent's usage was itemized by it.

For example, suppose a subpanel's feed circuit CT reads 0 Wh for an hour, perhaps because it is missing or clamped on the wrong wire, while the subpanel's own mains reads 36 Wh. The feed's Net Balance is floored at 0, and the subpanel's 36 Wh is still counted beneath it. The panel's tree then sums to its mains plus 36 Wh, and Vuegraf warns that the panel has circuits reading less than the devices nested beneath them, naming the feed. Likewise, placing a subpanel on a panel's mains when it is really fed through one of that panel's circuits counts it twice: once inside the circuit, and again below the mains. That drives the panel's Net Balance negative, and the warning names the device to check.

| Setting | Default | Meaning |
|---|---|---|
| `hierarchyBalanceEpsilonWatts` | `5.0` | A shortfall smaller than this average power is treated as measurement tolerance and not reported. |
| `hierarchyNegativeBalanceAbort` | `false` | When `true`, an hourly or daily period with a larger shortfall is not recorded at all, raw readings included, and an error is logged, so the problem cannot go unnoticed. Only that period is dropped: live collection and `--historydays` carry on with the next. A closed period that does not balance won't start to, so it isn't retried; once the tree or meter is fixed, `--historydays` can fill it in. |

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
