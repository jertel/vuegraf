#!/usr/bin/env python3

__author__ = 'https://github.com/jertel'
__license__ = 'MIT'
__contributors__ = 'https://github.com/jertel/vuegraf/graphs/contributors'
__version__ = '1.7.2'
__versiondate__ = '2024/12/21'
__maintainer__ = 'https://github.com/jertel'
__github__ = 'https://github.com/jertel/vuegraf'
__status__ = 'Production'

import datetime
import json
import signal
import sys
import time
import traceback
from threading import Event
import argparse
import pytz

# InfluxDB v1
import influxdb

# InfluxDB v2
import influxdb_client

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

# flush=True helps when running in a container without a tty attached
# (alternatively, "python -u" or PYTHONUNBUFFERED will help here)
def log(level, msg):
    now = datetime.datetime.now(datetime.UTC)
    print('{} | {} | {}'.format(now, level.ljust(5), msg), flush=True)

def debug(msg):
    if args.debug:
        log('DEBUG', msg)

def error(msg):
    log('ERROR', msg)

def info(msg):
    log('INFO', msg)

def verbose(msg):
    if args.verbose:
        log('VERB', msg)

def handleExit(signum, frame):
    global running
    error('Caught exit signal')
    running = False
    pauseEvent.set()

def getConfigValue(key, defaultValue):
    if key in config:
        return config[key]
    return defaultValue

def populateDevices(account):
    deviceIdMap = {}
    account['deviceIdMap'] = deviceIdMap
    channelIdMap = {}
    account['channelIdMap'] = channelIdMap
    devices = account['vue'].get_devices()
    for device in devices:
        device = account['vue'].populate_device_properties(device)
        deviceIdMap[device.device_gid] = device
        for chan in device.channels:
            key = '{}-{}'.format(device.device_gid, chan.channel_num)
            if chan.name is None and chan.channel_num == '1,2,3':
                chan.name = device.device_name
            channelIdMap[key] = chan
            info('Discovered new channel: {} ({})'.format(chan.name, chan.channel_num))

def lookupDeviceName(account, device_gid):
    if device_gid not in account['deviceIdMap']:
        populateDevices(account)

    deviceName = '{}'.format(device_gid)
    if device_gid in account['deviceIdMap']:
        deviceName = account['deviceIdMap'][device_gid].device_name
    return deviceName

def lookupChannelName(account, chan):
    if chan.device_gid not in account['deviceIdMap']:
        populateDevices(account)

    deviceName = lookupDeviceName(account, chan.device_gid)
    name = '{}-{}'.format(deviceName, chan.channel_num)

    try:
        num = int(chan.channel_num)
        if 'devices' in account:
            for device in account['devices']:
                if 'name' in device and device['name'] == deviceName:
                    if 'channels' in device and len(device['channels']) >= num:
                        name = device['channels'][num - 1]
                        break
    except:
        if chan.channel_num == '1,2,3':
            name = deviceName

    return name

def createDataPoint(account, chanName, watts, timestamp, detailed):
    dataPoint = None
    if influxVersion == 2:
        dataPoint = influxdb_client.Point('energy_usage') \
            .tag('account_name', account['name']) \
            .tag('device_name', chanName) \
            .tag(tagName, detailed) \
            .field('usage', watts) \
            .time(time=timestamp)
    else:
        dataPoint = {
            'measurement': 'energy_usage',
            'tags': {
                'account_name': account['name'],
                'device_name': chanName,
                tagName: detailed,
            },
            'fields': {
                'usage': watts,
            },
            'time': timestamp
        }
    return dataPoint

def dumpPoints(label, usageDataPoints):
    if args.debug:
        debug(label)
        for point in usageDataPoints:
            debug('  {}'.format(point.to_line_protocol()))

def getLastDBTimeStamp(chanName, pointType, fooStartTime, fooStopTime, fooHistoryFlag):
    timeStr = ''
    # Get timestamp of last record in database
    # Influx v2
    if influxVersion == 2:
        timeCol = '_time'
        result = query_api.query('from(bucket:"' + bucket + '") ' +
             '|> range(start: -3w) ' +
             '|> filter(fn: (r) => ' +
             '  r._measurement == "energy_usage" and ' +
             '  r.' + tagName + ' == "' + pointType + '" and ' +
             '  r._field == "usage" and ' +
             '  r.device_name == "' + chanName + '")' +
             '|> last()')

        if len(result) > 0 and len(result[0].records) > 0:
            lastRecord = result[0].records[0]
            timeStr = lastRecord['_time'].isoformat()
    else: # Influx v1
        result = influx.query('select last(usage), time from energy_usage where (device_name = \'' + chanName + '\' AND ' + tagName + ' = \'' + pointType + '\')')
        if len(result) > 0:
            timeStr = next(result.get_points())['time']

    if len(timeStr) > 0:
        timeStr = timeStr[:26]
        if not timeStr.endswith('Z') and not timeStr[len(timeStr)-6] in ('+',"-"):
            timeStr = timeStr + 'Z'

        dbLastRecordTime = datetime.datetime.strptime(timeStr, '%Y-%m-%dT%H:%M:%S%z').replace(tzinfo=datetime.timezone.utc)
        dbLastRecordTime = dbLastRecordTime.replace(microsecond=0)

        if pointType == tagValue_minute:
            if dbLastRecordTime < (fooStopTime - datetime.timedelta(minutes=2,seconds=fooStopTime.second)):
                fooHistoryFlag = True
                fooStartTime = dbLastRecordTime + datetime.timedelta(minutes=1)
                if int((fooStopTime - fooStartTime).total_seconds()) > 604800:      # 7 Days
                    fooStartTime = fooStopTime - datetime.timedelta(minutes=10080)  # 7 Days
                if int((fooStopTime - fooStartTime).total_seconds()) > 43200:       # 12 Hours
                    fooStopTime = fooStartTime + datetime.timedelta(minutes=720)    # 12 Hours

        if pointType == tagValue_second:
            if dbLastRecordTime < (fooStartTime - datetime.timedelta(seconds=2)):
                fooHistoryFlag = True
                fooStartTime = (dbLastRecordTime + datetime.timedelta(seconds=1)).replace(microsecond=0)
                if int((fooStopTime - fooStartTime).total_seconds()) > 10800:        # 3 Hours
                    fooStartTime = fooStopTime - datetime.timedelta(seconds=10800)   # 3 Hours
                if int((fooStopTime - fooStartTime).total_seconds()) > 3600:         # 1 Hour
                    fooStopTime = fooStartTime + datetime.timedelta(seconds=3600) # 1 Hour
    else:
        if pointType == tagValue_minute:
            fooStartTime = fooStartTime - datetime.timedelta(days=7)
            fooStopTime = fooStartTime + datetime.timedelta(hours=12)
            fooHistoryFlag = True
        elif pointType == tagValue_second:
            fooStartTime = fooStartTime - datetime.timedelta(hours=3)
            fooStopTime = fooStartTime + datetime.timedelta(hours=1)
            fooHistoryFlag = True

    return fooStartTime, fooStopTime, fooHistoryFlag

def extractDataPoints(device, usageDataPoints, pointType=None, historyStartTime=None, historyEndTime=None):
    excludedDetailChannelNumbers = ['Balance', 'TotalUsage']
    minutesInAnHour = 60
    secondsInAMinute = 60
    wattsInAKw = 1000

    for chanNum, chan in device.channels.items():
        if chan.nested_devices:
            for gid, nestedDevice in chan.nested_devices.items():
                extractDataPoints(nestedDevice, usageDataPoints, pointType, historyStartTime, historyEndTime)

        chanName = lookupChannelName(account, chan)

        kwhUsage = chan.usage
        if kwhUsage is not None:
            if pointType is None:
                minuteHistoryStartTime, stopTimeMin, minuteHistoryEnabled = getLastDBTimeStamp(chanName, tagValue_minute, stopTime, stopTime, False)
                if not minuteHistoryEnabled or chanNum in excludedDetailChannelNumbers:
                    watts = float(minutesInAnHour * wattsInAKw) * kwhUsage
                    timestamp = stopTime.replace(second=0)
                    usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, tagValue_minute))
                elif chanNum not in excludedDetailChannelNumbers and historyStartTime is None:
                    # Collect minutes history (if neccessary, never during history collection)
                    info('Get minute details; device="{}"; start="{}"; stop="{}"'.format(chanName, minuteHistoryStartTime, stopTimeMin))
                    usage, usage_start_time = account['vue'].get_chart_usage(chan, minuteHistoryStartTime, stopTimeMin, scale=Scale.MINUTE.value, unit=Unit.KWH.value)
                    usage_start_time = usage_start_time.replace(second=0,microsecond=0)
                    index = 0
                    for kwhUsage in usage:
                        if kwhUsage is None:
                            continue
                        timestamp = usage_start_time + datetime.timedelta(minutes=index)
                        watts = float(minutesInAnHour * wattsInAKw) * kwhUsage
                        usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, tagValue_minute))
                        index += 1
                    minuteHistoryEnabled = False
            elif pointType == tagValue_day or pointType == tagValue_hour :
                watts = kwhUsage * 1000
                timestamp = historyStartTime
                usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, pointType))

        if chanNum in excludedDetailChannelNumbers:
            continue

        if collectDetails and detailedSecondsEnabled and historyStartTime is None:
            # Collect seconds (once per hour, never during history collection)
            secHistoryStartTime, stopTimeSec, secondHistoryEnabled = getLastDBTimeStamp(chanName, tagValue_second, detailedStartTime, stopTime, detailedSecondsEnabled)
            verbose('Get second details; device="{}"; start="{}"; stop="{}"'.format(chanName, secHistoryStartTime, stopTimeSec))
            usage, usage_start_time = account['vue'].get_chart_usage(chan, secHistoryStartTime, stopTimeSec, scale=Scale.SECOND.value, unit=Unit.KWH.value)
            usage_start_time = usage_start_time.replace(microsecond=0)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    continue
                timestamp = usage_start_time + datetime.timedelta(seconds=index)
                watts = float(secondsInAMinute * minutesInAnHour * wattsInAKw) * kwhUsage
                usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, tagValue_second))
                index += 1

        # fetches historical Hour & Day data
        if historyStartTime is not None and historyEndTime is not None:
            verbose('Get historic details; device="{}"; start="{}"; stop="{}"'.format(chanName, historyStartTime,historyEndTime ))
            #Hours
            usage, usage_start_time = account['vue'].get_chart_usage(chan, historyStartTime, historyEndTime, scale=Scale.HOUR.value, unit=Unit.KWH.value)
            usage_start_time = usage_start_time.replace(minute=0, second=0, microsecond=0)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    continue
                timestamp = usage_start_time + datetime.timedelta(hours=index)
                watts = kwhUsage * 1000
                usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, tagValue_hour))
                index += 1
            #Days
            usage, usage_start_time = account['vue'].get_chart_usage(chan, historyStartTime, historyEndTime, scale=Scale.DAY.value, unit=Unit.KWH.value)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    continue
                timestamp = usage_start_time.astimezone(accountTimeZone) + datetime.timedelta(days=index)
                timestamp = timestamp.replace(hour=23, minute=59, second=59,microsecond=0)
                timestamp = timestamp.astimezone(pytz.UTC)
                watts =   kwhUsage * 1000
                usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, tagValue_day))
                index += 1

startupTime = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
try:
    #argparse includes default -h / --help as command line input
    parser = argparse.ArgumentParser(
        prog='vuegraf.py',
        description='Veugraf retrieves energy usage data from commerical cloud servers and inserts it into a self-hosted InfluxDB database.',
        epilog='For more information visit: ' + __github__
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
    args = parser.parse_args()
    info('Starting Vuegraf version {}'.format(__version__))

    config = {}
    with open(args.configFilename) as configFile:
        config = json.load(configFile)

    influxVersion = 1
    if 'version' in config['influxDb']:
        influxVersion = config['influxDb']['version']

    bucket = ''
    write_api = None
    query_api = None
    sslVerify = True

    if 'ssl_verify' in config['influxDb']:
        sslVerify = config['influxDb']['ssl_verify']

    if influxVersion == 2:
        info('Using InfluxDB version 2')
        bucket = config['influxDb']['bucket']
        org = config['influxDb']['org']
        token = config['influxDb']['token']
        url= config['influxDb']['url']
        influx2 = influxdb_client.InfluxDBClient(
           url=url,
           token=token,
           org=org,
           verify_ssl=sslVerify
        )
        write_api = influx2.write_api(write_options=influxdb_client.client.write_api.SYNCHRONOUS)
        query_api = influx2.query_api()

        if args.resetdatabase:
            info('Resetting database')
            delete_api = influx2.delete_api()
            start = '1970-01-01T00:00:00Z'
            stop = startupTime.isoformat(timespec='seconds').replace("+00:00", "") + 'Z'
            delete_api.delete(start, stop, '_measurement="energy_usage"', bucket=bucket, org=org)
    else:
        info('Using InfluxDB version 1')

        sslEnable = False
        if 'ssl_enable' in config['influxDb']:
            sslEnable = config['influxDb']['ssl_enable']

        # Only authenticate to ingress if 'user' entry was provided in config
        if 'user' in config['influxDb']:
            influx = influxdb.InfluxDBClient(host=config['influxDb']['host'], port=config['influxDb']['port'], username=config['influxDb']['user'], password=config['influxDb']['pass'], database=config['influxDb']['database'], ssl=sslEnable, verify_ssl=sslVerify)
        else:
            influx = influxdb.InfluxDBClient(host=config['influxDb']['host'], port=config['influxDb']['port'], database=config['influxDb']['database'], ssl=sslEnable, verify_ssl=sslVerify)

        influx.create_database(config['influxDb']['database'])

        if args.resetdatabase:
            info('Resetting database')
            influx.delete_series(measurement='energy_usage')

    # Get Influx Tag information
    tagName = 'detailed'
    if 'tagName' in config['influxDb']:
        tagName = config['influxDb']['tagName']
    tagValue_second = True
    if 'tagValue_second' in config['influxDb']:
        tagValue_second = config['influxDb']['tagValue_second']
    tagValue_minute = False
    if 'tagValue_minute' in config['influxDb']:
        tagValue_minute = config['influxDb']['tagValue_minute']
    tagValue_hour = 'Hour'
    if 'tagValue_hour' in config['influxDb']:
        tagValue_hour = config['influxDb']['tagValue_hour']
    tagValue_day = 'Day'
    if 'tagValue_day' in config['influxDb']:
        tagValue_day = config['influxDb']['tagValue_day']
        
    maxHistoryDays = getConfigValue('maxHistoryDays', 720)
    historyDays = min(args.historydays, maxHistoryDays)
    history = historyDays > 0
    running = True
    signal.signal(signal.SIGINT, handleExit)
    signal.signal(signal.SIGHUP, handleExit)
    pauseEvent = Event()
    intervalSecs = getConfigValue('updateIntervalSecs', 60)
    detailedIntervalSecs = getConfigValue('detailedIntervalSecs', 3600)
    detailedDataEnabled = getConfigValue('detailedDataEnabled', False)
    detailedSecondsEnabled = detailedDataEnabled and getConfigValue('detailedDataSecondsEnabled', True)
    detailedHoursEnabled = detailedDataEnabled and getConfigValue('detailedDataHoursEnabled', True)
    info('Settings -> updateIntervalSecs: {}, detailedDataEnabled: {}, detailedIntervalSecs: {}, detailedDataHoursEnabled: {}, detailedDataSecondsEnabled: {}'.format(intervalSecs, detailedDataEnabled, detailedIntervalSecs, detailedHoursEnabled, detailedSecondsEnabled))
    info('Settings -> historyDays: {}, maxHistoryDays: {}'.format(historyDays, maxHistoryDays))    
    lagSecs = getConfigValue('lagSecs', 5)
    accountTimeZoneName = getConfigValue('timezone', None)
    accountTimeZone = pytz.timezone(accountTimeZoneName) if accountTimeZoneName is not None and accountTimeZoneName.upper() != "TZ" else None
    info('Settings -> timezone: {}'.format(accountTimeZone))
    detailedStartTime = startupTime
    pastDay = datetime.datetime.now(accountTimeZone)
    pastDay = pastDay.replace(hour=23, minute=59, second=59, microsecond=0)
    historyrun = history

    while running:
        usageDataPoints = []
        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        curDay = datetime.datetime.now(accountTimeZone)
        stopTime = now - datetime.timedelta(seconds=lagSecs)
        secondsSinceLastDetailCollection = (stopTime - detailedStartTime).total_seconds()
        collectDetails = detailedDataEnabled and detailedIntervalSecs > 0 and secondsSinceLastDetailCollection >= detailedIntervalSecs
        verbose('Starting next event collection; collectDetails={}; secondsSinceLastDetailCollection={}; detailedIntervalSecs={}'.format(collectDetails, secondsSinceLastDetailCollection, detailedIntervalSecs))

        for account in config['accounts']:
            if 'vue' not in account:
                account['vue'] = PyEmVue()
                account['vue'].login(username=account['email'], password=account['password'])
                info('Login completed')
                populateDevices(account)

            try:
                deviceGids = list(account['deviceIdMap'].keys())
                usages = account['vue'].get_device_list_usage(deviceGids, stopTime, scale=Scale.MINUTE.value, unit=Unit.KWH.value)
                if usages is not None:
                    for gid, device in usages.items():
                        extractDataPoints(device, usageDataPoints)

                if collectDetails and detailedHoursEnabled:
                    pastHour = stopTime - datetime.timedelta(hours=1)
                    pastHour = pastHour.replace(minute=00, second=00,microsecond=0)
                    verbose('Collecting previous hour: {} '.format(pastHour))
                    historyStartTime = pastHour
                    usages = account['vue'].get_device_list_usage(deviceGids, pastHour, scale=Scale.HOUR.value, unit=Unit.KWH.value)
                    if usages is not None:
                        for gid, device in usages.items():
                            extractDataPoints(device, usageDataPoints, tagValue_hour, historyStartTime)

                if pastDay.day != curDay.day:
                    usages = account['vue'].get_device_list_usage(deviceGids, pastDay, scale=Scale.DAY.value, unit=Unit.KWH.value)
                    historyStartTime = pastDay.astimezone(pytz.UTC)
                    verbose('Collecting previous day: {}Local - {}UTC,  '.format(pastDay, historyStartTime))
                    if usages is not None:
                        for gid, device in usages.items():
                            extractDataPoints(device, usageDataPoints,tagValue_day, historyStartTime)
                    pastDay = datetime.datetime.now(accountTimeZone)
                    pastDay = pastDay.replace(hour=23, minute=59, second=00, microsecond=0)

                if history:
                    stopTime = stopTime.astimezone(accountTimeZone)
                    info('Loading historical data: {} day(s) ago'.format(historyDays))
                    historyStartTime = stopTime - datetime.timedelta(historyDays)
                    historyStartTime = historyStartTime.replace(hour=00, minute=00, second=00, microsecond=000000)
                    while historyStartTime <= stopTime:
                        historyEndTime = min(historyStartTime  + datetime.timedelta(20), stopTime)
                        historyEndTime = historyEndTime.replace(hour=23, minute=59, second=59,microsecond=0)
                        verbose('    {}  -  {}'.format(historyStartTime,historyEndTime))
                        for gid, device in usages.items():
                            extractDataPoints(device, usageDataPoints, 'History', historyStartTime, historyEndTime)
                        if not running:
                            break
                        historyStartTime = historyEndTime + datetime.timedelta(1)
                        historyStartTime = historyStartTime.replace(hour=00, minute=00, second=00, microsecond=000000)
                        # Write to database after each historical batch to prevent timeout issues on large history intervals.
                        info('Submitting datapoints to database; account="{}"; points={}'.format(account['name'], len(usageDataPoints)))
                        dumpPoints("Sending to database", usageDataPoints)
                        if influxVersion == 2:
                            write_api.write(bucket=bucket, record=usageDataPoints)
                        else:
                            influx.write_points(usageDataPoints,batch_size=5000)
                        usageDataPoints = []
                        pauseEvent.wait(5)
                    history = False

                if not running:
                    break

                if not historyrun:
                    info('Submitting datapoints to database; account="{}"; points={}'.format(account['name'], len(usageDataPoints)))
                    dumpPoints("Sending to database", usageDataPoints)
                    if influxVersion == 2:
                        write_api.write(bucket=bucket, record=usageDataPoints)
                    else:
                        influx.write_points(usageDataPoints,batch_size=5000)

                # Resuming logging of normal datapoints after history collection has completed.
                if not history and historyrun:
                    historyrun = False

            except:
                error('Failed to record new usage data: {}'.format(sys.exc_info()))
                traceback.print_exc()

        if collectDetails:
            detailedStartTime = stopTime + datetime.timedelta(seconds=1)
        pauseEvent.wait(intervalSecs)

    info('Finished')
except SystemExit as e:
    #If sys.exit was 2, then normal syntax exit from help or bad command line, no error message
    if e.code == 0 or e.code == 2:
        quit(0)
    else:
        error('Fatal error: {}'.format(sys.exc_info()))
        traceback.print_exc()
