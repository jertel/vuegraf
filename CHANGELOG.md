# 1.TBD.TBD

## Breaking changes
- TBD

## New features
- Refactor and improve history and detailed data collection including consistent timezone usage - @cooldil
- Add --dryrun CLI arg for skipping writes to InfluxDB - @garthweb
- New configuration option to include station_name field in all new InfluxDB datapoints, to distinguish similarly named channel devices within a single account - @garthweb

## Other changes
- Upgrade to pyemvue 0.18.6 - @jertel
- Upgrade to influxdb_client 1.48.0 - @jertel
- Upgrade to influxdb 5.3.2 - @jertel
- Fix --debug CLI arg error when using InfluxDB v1 - @garthweb

# 1.7.2

## Breaking changes
- None

## New features
- None

## Other changes
- Fixed daily metric collection after a new month cycles - @cdolghier
- Upgrade to pyemvue 0.18.1 - @jertel

# 1.7.1

## Breaking changes
- None

## New features
- None

## Other changes
- Added --debug arg to dump all points to console prior to sending to the database - @jertel
- On startup the new detailedDataHousesEnabled and detailedDataSecondsEnabled values will be printed to console - @jertel
- On startup the version will be printed to console - @jertel
- Fixed issue when hourly and historic data was collected it was discarding the previous minute data - @jertel
- Removed unused --quiet arg - @jertel
- Removed unnecessary --version arg - @jertel
- Refactored --verbose flag to properly use logging level - @jertel
- Removed unnecesary, duplicated queries - @jertel

# 1.7.0

## Breaking changes
- None

## New features
- Added `detailedDataSecondsEnabled` and `detailedDataHoursEnabled` to selectively fetch one or (or both) seconds- and hours- resolution data iff `detailedDataEnabled` = `true`
- Added `timezone` config to allow configuring the timezone according to which end-of-day is calculated.

## Other changes
- Fixed `--resetdatabase` (broken in 1.6.1)

# 1.6.1

## Breaking changes

- None

## New features

- None

## Other changes

- Upgrade to Python 3.12.1, replace deprecated datetime invocations - [#141](https://github.com/jertel/vuegraf/pull/141) - @jertel
- Fixed extractDataPoints to recurse correctly for nested devices - [#140](https://github.com/jertel/vuegraf/pull/140) - @cdolghier

# 1.6.0

## Breaking changes
- Replaced Minute with Hour as normal interval since history is limited to 7 days from Emporia on minute data
- argparse libary was added, must run `pip install -r requirements.txt` again in the src directory  (or pip3 based on install)

## New features
- Hour / Day historic data retrieval: allows for history of up to two years to be pulled. Assists in clean numbers/graphs to see daily monthly usage to compare against utilities reports.  
- Hour data runs with the get details time, default is 1 hour (3600 seconds).  Based on when the program is started, you may be almost 2 hours behind for get hour.
- Moved one-time parameters out of the json config file. Those parameters are now specified as command line arguments (--historydays, --resetdatabase).

## Other changes
- Started Changelog for this and future releases
- Added project metadata to `vuegraf.py`, values can be updated through github automations
- Added command line pairing with help syntax for all values, via argparse lib.
- Updated `requirements.txt` and setup.py with `argparse>= 1.4.0`
- Updated `vuegraf.json.sample` as history and reset database was moved to command line
- Updated Readme.md with above changes
- ran pylint and fixed
    Quote delimiter consistency to all '
    Whitespaces
    Extra lines

With special thanks to @gauthig for initiating these 1.6.0 changes!
