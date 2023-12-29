# 1.TBD.TBD

## Breaking changes
- TBD

## New features
- TBD

## Other changes
- TBD

# 1.6.1

## Breaking changes

- None

## New features

- None

## Other changes

- Upgrade to Python 3.12.1, replace deprecated datetime invocations - [#141](https://github.com/jertel/vuegraf/pull/141) - #jertel
- Fixed extractDataPoints to recurse correctly for nested devices - [#140](https://github.com/jertel/vuegraf/pull/140) - #cdolghier

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
