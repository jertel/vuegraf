# Overview

The [Emporia Vue](https://emporiaenergy.com "Emporia's Homepage") energy monitoring kit allows homeowners to monitor their electrical usage. It monitors the main feed consumption and up to 8 (or 16 in the newer version) individual branch circuits, and feeds that data back to the Emporia API server.

This project, Vuegraf, fetches those metrics from the Emporia Vue API host and stores the metrics into your own InfluxDB. When paired with Grafana you'll be able to:
* View your energy usage across all circuits on a single graph
* Create alerts to notify when certain energy usage thresholds are exceeded

This project is not affiliated with _emporia energy_ company.

# Dependencies

## Required
* [Emporia Vue](https://emporiaenergy.com "Emporia Energy") Account - Username and password for the Emporia Vue system are required.
* [Python 3](https://python.org "Python") - With Pip.
* [InfluxDB](https://influxdata.com "InfluxDB") - Host, port, and login credentials are required.

## Recommended
* [Grafana](https://grafana.com "Grafana") - Can be used to read metrics from the InfluxDB.

# Screenshots

The following screenshots are provided to illustrate the possibilities available after using Vuegraf. These were all taken from a functionaing Grafana installation.

A sample Grafana dashboard is shown below:

![Dashboard Example Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/dashboard.png?raw=true "Dashboard Example")

A single graph showing multiple overlayed circuits is shown below:

![Graph Example Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/graph.png?raw=true "Graph Example")

# Configuration
The configuration allows for the definition of multiple Emporia Vue accounts. This will only be useful to users that need to pull metrics from multiple accounts. This is not needed if you have multiple Vue devices in a single account. Vuegraf will find multiple devices on its own within each account.

The email address and password must match the credentials used when creating the Emporia Vue account in their mobile app.

Important: Ensure that sufficient protection is in place on this configuration file, since it contains the plain-text login credentials into the Emporia Vue account.

A [sample configuration file](https://github.com/jertel/vuegraf/blob/master/vuegraf.json.sample "Sample Vuegraf Configuration File") is provided in this repository, and details are described below.

## Minimal Configuration
The minimum configuration required to start Vuegraf is shown below.

InfluxDB v1:

```json
{
    "influxDb": {
        "host": "my.influxdb.hostname",
        "port": 8086,
        "user": "root",
        "pass": "root",
        "database": "vue",
        "reset": false
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

InfluxDB v2:

```json
{
    "influxDb": {
        "version": 2,
        "url": "http://my.influxdb.hostname:8086",
        "org": "vuegraf",
        "bucket": "vuegraf",
        "token": "veugraf-secret-token",
        "reset": false
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

### Ingesting Historical Data

If desired, it is possible to have Vuegraf import historical data. To do so, specify a new temporary parameter called `historyDays` inside the `influxDb` section, with an integer value greater than zero. Once restarted, One-minute data from the past `historyDays` days will be ingested into InfluxDB. Emporia currently retains this data for 7 days, and therefore `historyDays` must be less than or equal to `7`. If `historyDays` is set to `0`, no historical data will be ingested into InfluxDB.

IMPORTANT - If you restart Vuegraf with historyDays still set to a non-zero value then it will _again_ import history data. This will likely cause confusion with your data since you will now have duplicate/overlapping data. For best results, only enable historyDays > 0 for a single run, and then immediately set it back to 0 to avoid this duplicated import data scenario.

### Channel Names

To provide more user-friendly names of each Vue device and branch circuit, the following device configuration can be added to the configuration file, within the account block. List each device and circuit in the order that you added them to the Vue mobile app. The channel names do not need to match the names specified in the Vue mobile app but the device names must match. The below example shows two 8-channel Vue devices for a home with two breaker panels.

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

### Per-second Data Details

By default, Vuegraf will poll every minute to collect the energy usage value over the past 60 seconds. This results in a single value being capture per minute per channel, or 60 values per hour per channel. If you also would like to see per-second values, you can enable the detailed collection, which is polled once per hour, and backfilled over the previous 3600 seconds. This API call is very expensive on the Emporia servers, so it should not be polled more frequently than once per hour. To enable this detailed data, add (or update) the top-level `detailedDataEnabled` configuration value with a value of `true`.

```
detailedDataEnabled: true
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

# Running
Vuegraf can be run either as a host process, or as a container (recommended).

## Host Process

Ensure Python 3 and Pip are both installed. Install the required dependencies:

```sh
pip install -r src/requirements.txt
```
or, on some Linux installations:

```sh
pip3 install -r src/requirements.txt
```


Then run the program via Python, specifying the JSON configuration file path as the only argument:

```sh
python src/vuegraf/vuegraf.py vuegraf.json
```
or, on some Linux installations:
```sh
python3 src/vuegraf/vuegraf.py vuegraf.json
```

## Container (recommended)

A Docker container is provided at [hub.docker.com](https://hub.docker.com/r/jertel/vuegraf). Refer to the command below to launch Vuegraf as a container. This assumes you have create a folder called vuegraf and placed the vuegraf.json file inside of it.

```sh
docker run --name vuegraf -d -v /home/myusername/vuegraf:/opt/vuegraf/conf jertel/vuegraf
```

If you are new to Docker, the next two commands will help you get the InfluxDB (version 1) and Grafana containers up and running, assuming you have Docker installed and running already. In the above config example, your influxdb host name will be your host's real IP (*not* localhost or 127.0.0.1).

```sh
docker run -d --name influxdb -v /home/myusername/vuegraf:/var/lib/influxdb -p 8086:8086 influxdb:1.8-alpine
docker run -d --name grafana -v /home/myusername/vuegraf:/var/lib/grafana -p 3000:3000 grafana/grafana
```

### Docker Compose

For those that want to run Vuegraf using Docker Compose, the following files have been included: `docker-compose.yaml.template` and `docker-compose-run.sh`. These assume InfluxDB version 1 will be utilized. Copy the`docker-compose.yaml.template` file to a new file called `docker-compose.yaml`. In the newly copied file, `vuegraf.volumes` values will need to be changed to the same directory you have created your vuegraf.json file. Additionally, adjust the persistent host storage path for the Grafana and InfluxDB data volumes.

Finally run the `docker-compose-run.sh` script to start up the multi-container application. 

```sh
./docker-compose-run.sh
```

# Grafana

Use [Grafana](https://grafana.com "Grafana") to visualize the data collected by Vuegraf. A sample [dashboard.json](https://github.com/jertel/vuegraf/blob/master/dashboard.json) file is provided with this project, to get started. However, this sample dashboard is only compatible with InfluxDB version 1.

If you only have one Vue device you should remove the Left/Right panel references.

Refer to the screenshots below for examples on how to define the InfluxDB data source, graphs, and alerts.

NOTE: The energy_usage measurement includes two types of data:

- `detailed = true` represents backfilled per-second data that is optionally queried from Emporia once every hour.
- `detailed = false` represents the per-minute average data that is collected every minute.

When building graphs that show a sum of the energy usage, be sure to only include either detailed=true or detailed=false, otherwise your summed values will be higher than expected.


![Grafana Data Source Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/datasource.png?raw=true "Data Source Example")

A graph query is shown below, showing a simple filter to pull data for a specific circuit.

![Query Example Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/query.png?raw=true "Query Example")

Grafana also supports alerts, with a number of alert channels, such as Email or Slack.

![Alert Example Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/alert.png?raw=true "Alert Example")

# License

Vuegraf is distributed under the MIT license.

See [LICENSE](https://github.com/jertel/vuegraf/blob/master/LICENSE) for more information.
