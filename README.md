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
The minimum configuration required to start Vuegraf is shown below:

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
            "password": "my-emporia-password",
        }
    ]
}
```

## Advanced Configuration
To provide more user-friendly names of each Vue device and branch circuit, the following device configuration can be added to the configuration file, within the account block. List each device and circuit in the order that you added them to the Vue mobile app. The channel names do not need to match the names specified in the Vue mobile app but the device names must match.

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
```

# Running
Vuegraf can be run either as a host process, or as a container.

## Host

Ensure Python 3 and Pip are both installed. Install the required dependencies:

```sh
pip install -r requirements.txt
```

Then run the program via Python, specifying the JSON configuration file path as the only argument:

```sh
python vuegraf.py vuegraf.json
```

## Container

A Docker container is provided at [hub.docker.com](https://hub.docker.com/r/jertel/vuegraf). Refer to the command below to launch Vuegraf as a container.

```sh
docker run --name vuegraf -d -v /home/myusername/vuegraf.conf:/opt/vuegraf/conf/vuegraf.json jertel/vuegraf
```

# Grafana

Use [Grafana](https://grafana.com "Grafana") to visualize the data collected by Vuegraf. A sample [dashboard.json](https://github.com/jertel/vuegraf/blob/master/dashboard.json) file is provided with this project, to get started. If you only have one Vue device you should remove the Left/Right panel references.

Refer to the screenshots below for examples on how to define the InfluxDB data source, graphs, and alerts.

![Grafana Data Source Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/datasource.png?raw=true "Data Source Example")

A graph query is shown below, showing a simple filter to pull data for a specific circuit.

![Query Example Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/query.png?raw=true "Query Example")

Grafana also supports alerts, with a number of alert channels, such as Email or Slack.

![Alert Example Screenshot](https://github.com/jertel/vuegraf/blob/master/screenshots/alert.png?raw=true "Alert Example")

# License

Vuegraf is distributed under the MIT license.

See [LICENSE](https://github.com/jertel/vuegraf/blob/master/LICENSE) for more information.
