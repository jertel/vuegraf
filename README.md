# Overview

The [Emporia Vue](https://emporiaenergy.com "Emporia's Homepage") energy monitoring kit allows homeowners to monitor their electrical usage. It monitors the main feed consumption and up to 8 (or 16 in the newer version) individual branch circuits, and feeds that data back to the Emporia API server.

This project, Vuegraf, fetches those metrics from the Emporia Vue API host and stores the metrics into your own InfluxDB. After installation you will be able to:
* View your energy usage across all circuits on a single graph
* Create alerts to notify when certain energy usage thresholds are exceeded

This project is not affiliated with _emporia energy_ company.

# Dependencies

* [Emporia Vue](https://emporiaenergy.com "Emporia Energy") Account - Username and password for the Emporia Vue system are required.
* [Python 3](https://python.org "Python") - With Pip.
* [InfluxDB 2](https://influxdata.com "InfluxDB") - Host, port, org, bucket, and token are all required.

# Influx

## Setup

If you do not yet have a running InfluxDB 2 instance, you will need to set one up. You can do this very quickly by launching an InfluxDB 2 Docker container as follows:

```
mkdir -p /opt/data/influxdb2
docker run -v /opt/data/influxdb2:/var/lib/influxdb2 -p 8086:8086 -e INFLUXD_SESSION_LENGTH=432000 --name influxdb influxdb
```

Substitute an appropriate host path for the `/opt/data/influxdb2` location above. Once running, access the web UI at `http://localhost:8086`. It will prompt you for a username, password, organization name, and bucket name. The rest of this document assumes you have entered the word `vuegraf` for all of these inputs.

Note that the default session timeout for Influx is only 60 minutes, so this command increases the login session to 300 days.

Once logged in, go to the _Load Data -> API Tokens_ screen and generate a new All Access token with the description of _vuegraf_. Copy the generated token for use in the rest of this document, specifically when referenced as `<my-influx-token>`.

## Dashboard

By default, a new InfluxDB instance will not have any dashboards loaded. You will need to import the included Influx JSON template, or create your own dashboard in order to visualize your energy usage.

The included template file named `influx_dashboard.json` includes the provided dashboard and accompanying variables to reproduce the visualizations shown below. This dashboard assumes your main device name contains the word `Panel`, such as `House Panel`, or `Right Panel`. If it does not, the Flux queries will need to be adjusted manually to look for your device's name.

![Influx Dashboard Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/influx_dashboard.png?raw=true "Influx Dashboard")

You will need to apply this template file to your running InfluxDB instance. Copy the `influx_dashboard.json` file into your hosts' influxdb2 path. If you followed the Setup instructions above, the path would be `/opt/data/influxdb2`. The below command can be used to perform this step. This command assumes you are running Influx in a container named `influxdb`.

```
docker exec influxdb influx -f /var/lib/influxdb2/influx_dashboard.json --org vuegraf -t <my-influx-token>
```

Replace the `<my-influx-token>` with the All Access Token you generated in the Influx _Load Data -> API Tokens_ screen.

You're now ready to proceed with the Vuegraf configuration and startup.

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
        "token": "<my-secret-token>",
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

If you are new to Docker, the next following command will help you get the InfluxDB container up and running, assuming you have Docker installed and running already. In the above config example, your influxdb URL will include your host's real IP (*not* localhost or 127.0.0.1).

```sh
docker run -d --name influxdb -v /home/myusername/vuegraf:/var/lib/influxdb2 -p 8086:8086 influxdb
```

## Alerts

The included dashboard template does not contain any alerts, since each user will have very specific criteria and devices in mind for alerting. However, the below screenshots can help illustrate how a fully functioning alert and notification rule might look.

This alert was edited via the text (Flux) interface since the alert edit UI does not yet accommodate advanced alerting inputs.

![Influx Alert Edit](https://github.com/jertel/vuegraf/blob/master/screenshots/alert_edit.png?raw=true "Influx Alert")

This notification rule provides an example of how you can have several alerts change the status to crit, but only a single notification rule is required to transmit notifications to external endpoints (such as email or Slack).

![Influx Notification Rule](https://github.com/jertel/vuegraf/blob/master/screenshots/notification_rule.png?raw=true "Influx Notification Rule")


# Additional Topics

## Per-second Data Details

By default, Vuegraf will poll every minute to collect the energy usage value over the past 60 seconds. This results in a single value being capture per minute per channel, or 60 values per hour per channel. If you also would like to see per-second values, you can enable the detailed collection, which is polled once per hour, and backfilled over the previous 3600 seconds. This API call is very expensive on the Emporia servers, so it should not be polled more frequently than once per hour. To enable this detailed data, add (or update) the top-level `detailedDataEnabled` configuration value with a value of `true`.

```
detailedDataEnabled: true
```

Again:

- `detailed = True` represents backfilled per-second data that is optionally queried from Emporia once every hour.
- `detailed = False` represents the per-minute average data that is collected every minute.

When building graphs that show a sum of the energy usage, be sure to only include either detailed=true or detailed=false, otherwise your summed values will be higher than expected. Detailed data will take more time for the graphs to query due to the extra data involved. By default, it is set to False so most users can ignore this note.

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
  -v /opt/data/influxdb:/var/lib/influxdb \
  -v /opt/data/influxdb2:/var/lib/influxdb2 \
  -e DOCKER_INFLUXDB_INIT_MODE=upgrade \
  -e DOCKER_INFLUXDB_INIT_USERNAME=vuegraf \
  -e DOCKER_INFLUXDB_INIT_PASSWORD=vuegraf \
  -e DOCKER_INFLUXDB_INIT_ORG=vuegraf \
  -e DOCKER_INFLUXDB_INIT_BUCKET=vuegraf \
  -e DOCKER_INFLUXDB_INIT_RETENTION=1y \
  influxdb
```

The upgrade should complete relatively quickly. For reference, a 7GB database, spanning several months, upgrades in about 15 seconds on SSD storage.

Monitor the console output and once the upgrade completes and the Influx server finishes starting, shut it down (CTRL+C) and then restart the Influx DB using the command referenced earlier in this document.

Login to the new Influx DB 2 UI from your web browser, using the _vuegraf / vuegraf_ credentials. Go into the _Load Data -> Buckets_ screen and rename the `vue/autogen` bucket to `vuegraf` via the Settings button.

Finally, apply the dashboard template as instructed earlier in this document.

# License

Vuegraf is distributed under the MIT license.

See [LICENSE](https://github.com/jertel/vuegraf/blob/master/LICENSE) for more information.
