# 2.TBD.TBD- Future

## Breaking changes
- TBD

## New features
- TBD

## Other changes


# 1.6.0 - Released

## Breaking changes
- Replaced Minute with Hour as normal interval since history is limited to 7 days from Emporia on minute data
- argparse libary was added, must run pip install -r requirements again in the src directory  (or pip3 based on install)

## New features
- Hour / Day data pulled.  Allows for history of 2 years to be pulled. Assists in clean numbers/graphs to see daily monthly usage to compare against utilities reports.  
- Hour data runs with the get details time, default is 1 hour (3600 seconds).  Based on when the program is started,
you may be almost 2 hours behind for get hour.
- Changed several one time parameters from the json config file to command line entries (History load days, reset database).
<b> Example Command Line usage
usage: vuegraf.py [-h] [--version] [-v] [-q] [--historydays HISTORYDAYS] [--resetdatabase] configFilename

positional arguments:
  configFilename        json config file

options:
  -h, --help            show this help message and exit
  --version             display version number
  -v, --verbose         verbose output - summaries
  -q, --quiet           do not print anything but errors
  --historydays HISTORYDAYS
                        Starts executin by pulling history of Hours and Day data for specified number of days.
                        example: --load-history-day 60
  --resetdatabase       Drop database and create a new one

## Other changes
- Started Changelog for this and future releases
- Added project metadata to main program - vuegraf.py, values can be updated through github automations
- Added command line paring with help syntaxt for all values - arge parse lib.
- Updated requirements.txt and setup.py with argparse>= 1.4.0
- Updated vuegraf.json.sample as history and reset database was moved to command line
- Updated Readme.md with above changes
- ran pylint and fixed
    Quote delimiter consitency to all '
    Whitespaces
    Extra lines



# 1.5.0
