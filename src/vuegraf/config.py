# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to loading configuration values.

import argparse
import datetime
import json
import logging


logger = logging.getLogger('vuegraf.config')


def setConfigDefault(config, key, value):
    if key not in config:
        config[key] = value


def getConfigValue(config, key):
    return config[key]


def getInfluxVersion(config):
    influxVersion = 1
    if 'version' in config['influxDb']:
        influxVersion = config['influxDb']['version']
    return influxVersion


def getInfluxTag(config):
    tagName = 'detailed'
    if 'tagName' in config['influxDb']:
        tagName = config['influxDb']['tagName']
    tagValue_second = 'True'
    if 'tagValue_second' in config['influxDb']:
        tagValue_second = config['influxDb']['tagValue_second']
    tagValue_minute = 'False'
    if 'tagValue_minute' in config['influxDb']:
        tagValue_minute = config['influxDb']['tagValue_minute']
    tagValue_hour = 'Hour'
    if 'tagValue_hour' in config['influxDb']:
        tagValue_hour = config['influxDb']['tagValue_hour']
    tagValue_day = 'Day'
    if 'tagValue_day' in config['influxDb']:
        tagValue_day = config['influxDb']['tagValue_day']
    return tagName, tagValue_second, tagValue_minute, tagValue_hour, tagValue_day


def initArgs():
    parser = argparse.ArgumentParser(
        prog='vuegraf.py',
        description='Vuegraf retrieves energy usage data from the Emporia servers and inserts it into a self-hosted InfluxDB database.',
        epilog='For more information visit: https://github.com/jertel/vuegraf'
        )
    parser.add_argument(
        'configFilename',
        help='JSON config file',
        type=str
        )
    parser.add_argument(
        '-v',
        '--verbose',
        help='Verbose output - shows additional collection information',
        action='store_true')
    parser.add_argument(
        '-d',
        '--debug',
        help='Debug output - shows all point data being collected and written to the DB (can be thousands of lines of output)',
        action='store_true')
    parser.add_argument(
        '--historydays',
        help='Starts execution by pulling history of Hours and Day data for specified number of days.  example: --historydays 60',
        type=int,
        default=0
        )
    parser.add_argument(
        '--resetdatabase',
        action='store_true',
        default=False,
        help='Drop database and create a new one. USE WITH CAUTION - WILL RESULT IN COMPLETE VUEGRAF DATA LOSS!')
    parser.add_argument(
        '--dryrun',
        help='Read from the API and process the data, but do not write to influxdb',
        action='store_true',
        default=False
        )
    args = parser.parse_args()
    return args


# Configure logging
def initLogging(verbose):
    logger = logging.getLogger('vuegraf')
    if verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s | %(levelname)-5s | %(message)s')
    formatter.converter = lambda *args: datetime.datetime.now(datetime.UTC).timetuple()
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def initConfig():
    args = initArgs()
    config = {}
    with open(args.configFilename) as configFile:
        config = json.load(configFile)

    setConfigDefault(config, 'addStationField', False)
    setConfigDefault(config, 'detailedIntervalSecs', 3600)
    setConfigDefault(config, 'detailedDataEnabled', False)
    setConfigDefault(config, 'detailedDataDaysEnabled', True)
    setConfigDefault(config, 'detailedDataHoursEnabled', True)
    setConfigDefault(config, 'detailedDataSecondsEnabled', True)
    setConfigDefault(config, 'lagSecs', 5)
    setConfigDefault(config, 'timezone', None)
    setConfigDefault(config, 'maxHistoryDays', 720)
    setConfigDefault(config, 'updateIntervalSecs', 60)

    sanitized_config = config.copy()  # Create a sanitized copy for logging
    sanitized_config.pop('influxDb', None)  # Remove sensitive information from the config for logging
    sanitized_config.pop('accounts', None)  # Remove sensitive information from the config for logging

    config['args'] = args
    verbose = False
    if args.verbose or args.debug:
        verbose = True
    config['logger'] = initLogging(verbose)

    logger.info('Loaded settings; config={}'.format(sanitized_config))

    return config
