# 1.TBD.TBD

## Breaking changes
- TBD

## New features
- TBD

## Other changes
- TBD

# 1.6.0

## Breaking changes
- Replaced Minute with Hour as normal interval since history is limited to 7 days from Emporia on minute data
- argparse libary was added, must run pip `install -r requirements.txt` again in the src directory  (or pip3 based on install)

## New features
- Hour / Day historic data retrieval: allows for history of up to two years to be pulled. Assists in clean numbers/graphs to see daily monthly usage to compare against utilities reports.  
- Hour data runs with the get details time, default is 1 hour (3600 seconds).  Based on when the program is started, you may be almost 2 hours behind for get hour.
- Moved one-time parameters out of the json config file. Those parameters are now specified as command line arguments (History load days, reset database).

Example Command Line usage
```
vuegraf.py [-h] [--version] [-v] [-q] [--historydays HISTORYDAYS] [--resetdatabase] configFilename

positional arguments:
  configFilename        json config file

options:
  -h, --help            Show this help message and exit
      --version         Display version number
  -v, --verbose         Verbose output - summaries
  -q, --quiet           Do not print anything but errors
  --historydays HISTORYDAYS
                        Starts executing by pulling history of Hours and Day data for specified number of days.
                        example: --historydays 60
  --resetdatabase       Drop database and create a new one
```

## Other changes
- Started Changelog for this and future releases
- Added project metadata to main program - vuegraf.py, values can be updated through github automations
- Added command line pairing with help syntax for all values, via argparse lib.
- Updated requirements.txt and setup.py with argparse>= 1.4.0
- Updated vuegraf.json.sample as history and reset database was moved to command line
- Updated Readme.md with above changes
- ran pylint and fixed
    Quote delimiter consistency to all '
    Whitespaces
    Extra lines
