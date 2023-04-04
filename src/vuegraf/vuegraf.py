#!/usr/bin/env python3

import datetime
import json
import signal
import sys
import time
import traceback
from threading import Event

# InfluxDB v1
import influxdb

# InfluxDB v2
import influxdb_client

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

# flush=True helps when running in a container without a tty attached
# (alternatively, "python -u" or PYTHONUNBUFFERED will help here)
def log(level, msg):
    now = datetime.datetime.utcnow()
    print('{} | {} | {}'.format(now, level.ljust(5), msg), flush=True)

def info(msg):
    log("INFO", msg)

def error(msg):
    log("ERROR", msg)

def handleExit(signum, frame):
    global running
    error('Caught exit signal')
    running = False
    pauseEvent.set()

def getConfigValue(key, defaultValue):
    if key in config:
        return config[key]
    return defaultValue

# Reset config file if history or DB reset set
# Allows sequential runs without lossing data 
def setconfig(configname, configkey, configvalue) :
    with open(configFilename, 'r') as newconfigFile:
        newconfig = json.load(newconfigFile)
    newconfig[configname][configkey] = configvalue
    with open(configFilename, 'w') as configout:
        json.dump(newconfig, configout, indent=4)
    return()

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
            key = "{}-{}".format(device.device_gid, chan.channel_num)
            if chan.name is None and chan.channel_num == '1,2,3':
                chan.name = device.device_name
            channelIdMap[key] = chan
            info("Discovered new channel: {} ({})".format(chan.name, chan.channel_num))

def lookupDeviceName(account, device_gid):
    if device_gid not in account['deviceIdMap']:
        populateDevices(account)

    deviceName = "{}".format(device_gid)
    if device_gid in account['deviceIdMap']:
        deviceName = account['deviceIdMap'][device_gid].device_name
    return deviceName

def lookupChannelName(account, chan):
    if chan.device_gid not in account['deviceIdMap']:
        populateDevices(account)

    deviceName = lookupDeviceName(account, chan.device_gid)
    name = "{}-{}".format(deviceName, chan.channel_num)

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
        dataPoint = influxdb_client.Point("energy_usage") \
            .tag("account_name", account['name']) \
            .tag("device_name", chanName) \
            .tag("detailed", detailed) \
            .field("usage", watts) \
            .time(time=timestamp)
    else:
        dataPoint = {
            "measurement": "energy_usage",
            "tags": {
                "account_name": account['name'],
                "device_name": chanName,
                "detailed": detailed,
            },
            "fields": {
                "usage": watts,
            },
            "time": timestamp
        }
    return dataPoint

def extractDataPoints(device, usageDataPoints, historyStartTime=None, historyEndTime=None, histLoop=None):
    excludedDetailChannelNumbers = ['Balance', 'TotalUsage']
    minutesInAnHour = 60
    secondsInAMinute = 60
    wattsInAKw = 1000

    for chanNum, chan in device.channels.items():
        if chan.nested_devices:
            for gid, nestedDevice in chan.nested_devices.items():
                extractDataPoints(nestedDevice, usageDataPoints, historyStartTime, historyEndTime)

        chanName = lookupChannelName(account, chan)

        kwhUsage = chan.usage
        if kwhUsage is not None:
            watts = float(minutesInAnHour * wattsInAKw) * kwhUsage
            timestamp = stopTime
            usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, False))

        if chanNum in excludedDetailChannelNumbers:
            continue

        if collectDetails:
            #Seconds
            usage, usage_start_time = account['vue'].get_chart_usage(chan, detailedStartTime, stopTime, scale=Scale.SECOND.value, unit=Unit.KWH.value)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    continue
                timestamp = detailedStartTime + datetime.timedelta(seconds=index)
                watts = float(secondsInAMinute * minutesInAnHour * wattsInAKw) * kwhUsage
                usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, True))
                index += 1
        
        # fetches historical minute data
        if histLoop == "minute" :
            usage, usage_start_time = account['vue'].get_chart_usage(chan, historyStartTime, historyEndTime, scale=Scale.MINUTE.value, unit=Unit.KWH.value)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    continue
                timestamp = historyStartTime + datetime.timedelta(minutes=index)
                watts = float(minutesInAnHour * wattsInAKw) * kwhUsage
                usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, False))
        
                index += 1
        # fetches historical hour/day or just previous day
        if histLoop == "day-hour" or collectSummaries:
            historyStartTime = stopTime - datetime.timedelta(days=max(historyDays,1))
            historyStartTime = historyStartTime.replace( hour=00, minute=00, second=00, microsecond=00)
            #only used on history runs, otherwise is zero and will do next loop once.
            dayLoop = historyDays / 25
            while dayLoop >= 0  and historyStartTime < stopTime :
                #Fetches hour data
                historyEndTime = min(stopTime, historyStartTime + datetime.timedelta(days=25))
                historyEndTime = historyEndTime.replace( hour=23,minute=59, second=59, microsecond=999999)
                usage, usage_start_time = account['vue'].get_chart_usage(chan, historyStartTime, historyEndTime, scale=Scale.HOUR.value, unit=Unit.KWH.value)
                index = 0
                info('CollectSummaries; start="{}"; stop="{}"'.format(historyStartTime,historyEndTime ))
                for kwhUsage in usage:
                    if kwhUsage is None:
                        continue
                    timestamp = usage_start_time + datetime.timedelta(hours=index)
                    watts =   kwhUsage * 1000
                    usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, "Hour"))
                    print(datetime.datetime.now, )
                    index += 1
                #Fetches date data
                usage, usage_start_time = account['vue'].get_chart_usage(chan, historyStartTime, historyEndTime, scale=Scale.DAY.value, unit=Unit.KWH.value)
                index = 0
                for kwhUsage in usage:
                    if kwhUsage is None:
                        continue
                    timestamp = usage_start_time + datetime.timedelta(days=index)
                    watts =   kwhUsage * 1000
                    usageDataPoints.append(createDataPoint(account, chanName, watts, timestamp, "Day"))
                    index += 1 
                dayLoop = dayLoop -1
                historyStartTime = historyStartTime + datetime.timedelta(days=26)
startupTime = datetime.datetime.utcnow()
try:
    if len(sys.argv) != 2:
        print('Usage: python {} <config-file>'.format(sys.argv[0]))
        sys.exit(1)

    configFilename = sys.argv[1]
    config = {}
    with open(configFilename) as configFile:
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

        if config['influxDb']['reset']:
            info('Resetting database')
            delete_api = influx2.delete_api()
            start = "1970-01-01T00:00:00Z"
            stop = startupTime.isoformat(timespec='seconds')
            delete_api.delete(start, stop, '_measurement="energy_usage"', bucket=bucket, org=org)    
            setconfig('influxDb','reset',False) 
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

        print('RESET = ',config['influxDb']['reset'])
        if config['influxDb']['reset']:
            info('Resetting database')
            influx.delete_series(measurement='energy_usage')
            setconfig('influxDb','reset',False) 

    historyMinute = min(config['influxDb'].get('historyDays', 0), 7)
    historyDays = min(config['influxDb'].get('historyDays', 0), 720)
    history = historyDays > 0

    running = True
    signal.signal(signal.SIGINT, handleExit)
    signal.signal(signal.SIGHUP, handleExit)
    pauseEvent = Event()
    intervalSecs=getConfigValue("updateIntervalSecs", 60)
    detailedIntervalSecs=getConfigValue("detailedIntervalSecs", 3600)
    detailedDataEnabled=getConfigValue("detailedDataEnabled", False)
    summariesDataEnabled=getConfigValue('summariesDataEnabled', False)
    info('Settings -> updateIntervalSecs: {}, detailedEnabled: {}, detailedIntervalSecs: {}, summariesEnabled {}'.format(intervalSecs, detailedDataEnabled, detailedIntervalSecs, summariesDataEnabled))
    info(' History Days {}'.format(historyDays))
    lagSecs=getConfigValue("lagSecs", 5)
    detailedStartTime = startupTime
    #Only pull previous days Hourly and full day data and Full day after on next details loop after 00:05
    summariesDate = datetime.datetime.now() + datetime.timedelta(days=1)
    summariesDate = summariesDate.replace(hour=0, minute=5, second=00, microsecond=00)
    info('Set collectSummaries; {}'.format(summariesDate))
    while running:
        now = datetime.datetime.utcnow()
        stopTime = now - datetime.timedelta(seconds=lagSecs)
        collectDetails = detailedDataEnabled and detailedIntervalSecs > 0 and (stopTime - detailedStartTime).total_seconds() >= detailedIntervalSecs
        collectSummaries = summariesDataEnabled and datetime.datetime.now()  >= summariesDate
        for account in config["accounts"]:
            if 'vue' not in account:
                account['vue'] = PyEmVue()
                account['vue'].login(username=account['email'], password=account['password'])
                info('Login completed')
                populateDevices(account)

            try:
                deviceGids = list(account['deviceIdMap'].keys())
                usages = account['vue'].get_device_list_usage(deviceGids, stopTime, scale=Scale.MINUTE.value, unit=Unit.KWH.value)
                if usages is not None:
                    usageDataPoints = []
                    for gid, device in usages.items():
                        extractDataPoints(device, usageDataPoints)

                    if history:
                        for day in range(historyMinute):
                            histLoop = "minute"
                            info('Loading historical data - Minutes: {} day(s) ago'.format(day+1))
                            #Extract second 12h of day
                            historyStartTime = stopTime - datetime.timedelta(seconds=3600*24*(day+1)-43200)
                            historyEndTime = stopTime - datetime.timedelta(seconds=(3600*24*(day)))
                            for gid, device in usages.items():
                                extractDataPoints(device, usageDataPoints, historyStartTime, historyEndTime, histLoop)
                            pauseEvent.wait(5)
                            #Extract first 12h of day
                            historyStartTime = stopTime - datetime.timedelta(seconds=3600*24*(day+1))
                            historyEndTime = stopTime - datetime.timedelta(seconds=(3600*24*(day+1))-43200)
                            for gid, device in usages.items():
                                extractDataPoints(device, usageDataPoints, historyStartTime, historyEndTime, histLoop)
                            if not running:
                                break
                            pauseEvent.wait(5)

                        if summariesDataEnabled:
                            histLoop = "day-hour"
                            info('Loading historical data - Days/Hours/Months: {} day(s) ago'.format(historyDays))   
                            for gid, device in usages.items():
                                extractDataPoints(device, usageDataPoints, historyStartTime, historyEndTime, histLoop)
                            if not running:
                                break
                            pauseEvent.wait(5)
                        history = False
                        setconfig('influxDb','historyDays', 0)

                    if not running:
                        break

                    info('Submitting datapoints to database; account="{}"; points={}; Details="{}"; Summaries="{}"'.format(account['name'], 
                                                            len(usageDataPoints),collectDetails, collectSummaries ))
                    if influxVersion == 2:
                        write_api.write(bucket=bucket, record=usageDataPoints)
                    else:
                        influx.write_points(usageDataPoints,batch_size=5000)

            except:
                error('Failed to record new usage data: {}'.format(sys.exc_info())) 
                traceback.print_exc()

        if collectDetails:
            detailedStartTime = stopTime + datetime.timedelta(seconds=1)
        if collectSummaries:
            summariesDate = datetime.datetime.now() + datetime.timedelta(days=1)
            summariesDate = summariesDate.replace(hour=0, minute=5, second=00, microsecond=00)
            info('Reset collectSummaries; {}'.format(summariesDate))
        pauseEvent.wait(intervalSecs)

    info('Finished')
except:
    error('Fatal error: {}'.format(sys.exc_info())) 
    traceback.print_exc()


