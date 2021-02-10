#!/usr/bin/env python3

import datetime
import json
import signal
import sys
import time
from threading import Event
from influxdb import InfluxDBClient
from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

if len(sys.argv) != 2:
    print('Usage: python {} <config-file>'.format(sys.argv[0]))
    sys.exit(1)

configFilename = sys.argv[1]
config = {}
with open(configFilename) as configFile:
    config = json.load(configFile)

# Only authenticate to ingress if 'user' entry was provided in config
if 'user' in config['influxDb']:
    influx = InfluxDBClient(host=config['influxDb']['host'], port=config['influxDb']['port'], username=config['influxDb']['user'], password=config['influxDb']['pass'], database=config['influxDb']['database'])
else:
    influx = InfluxDBClient(host=config['influxDb']['host'], port=config['influxDb']['port'], database=config['influxDb']['database'])

influx.create_database(config['influxDb']['database'])

running = True

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
    if 'devices' in account:
        for device in account['devices']:
            if 'name' in device and device['name'] == deviceName:
                try:
                    num = int(chan.channel_num)
                    if 'channels' in device and len(device['channels']) >= num:
                        name = device['channels'][num - 1]
                except:
                    name = deviceName
    return name

signal.signal(signal.SIGINT, handleExit)
signal.signal(signal.SIGHUP, handleExit)

pauseEvent = Event()

INTERVAL_SECS=60
LAG_SECS=5


if config['influxDb']['reset']:
    info('Resetting database')
    influx.delete_series(measurement='energy_usage')


while running:
    for account in config["accounts"]:
        tmpEndingTime = datetime.datetime.utcnow() - datetime.timedelta(seconds=LAG_SECS)

        if 'vue' not in account:
            account['vue'] = PyEmVue()
            account['vue'].login(username=account['email'], password=account['password'])
            info('Login completed')

            populateDevices(account)

            account['end'] = tmpEndingTime

            start = account['end'] - datetime.timedelta(seconds=INTERVAL_SECS)

            result = influx.query('select last(usage), time from energy_usage where account_name = \'{}\''.format(account['name']))
            if len(result) > 0:
                timeStr = next(result.get_points())['time'][:26] + 'Z'
                tmpStartingTime = datetime.datetime.strptime(timeStr, '%Y-%m-%dT%H:%M:%S.%fZ')
                if tmpStartingTime > start:
                    start = tmpStartingTime
        else:
            start = account['end'] + datetime.timedelta(seconds=1)
            account['end'] = tmpEndingTime

        try:
            deviceGids = list(account['deviceIdMap'].keys())
            channels = account['vue'].get_devices_usage(deviceGids, None, scale=Scale.DAY.value, unit=Unit.KWH.value)
            usageDataPoints = []
            device = None
            secondsInAnHour = 3600
            wattsInAKw = 1000
            for chan in channels:
                chanName = lookupChannelName(account, chan)

                usage, usage_start_time = account['vue'].get_chart_usage(chan, start, account['end'], scale=Scale.SECOND.value, unit=Unit.KWH.value)
                index = 0
                for kwhUsage in usage:
                    if kwhUsage is not None:
                        watts = float(secondsInAnHour * wattsInAKw) * kwhUsage
                        dataPoint = {
                            "measurement": "energy_usage",
                            "tags": {
                                "account_name": account['name'],
                                "device_name": chanName,
                            },
                            "fields": {
                                "usage": watts,
                            },
                            "time": start + datetime.timedelta(seconds=index)
                        }
                        index = index + 1
                        usageDataPoints.append(dataPoint)

            info('Submitted datapoints to database; account="{}"; points={}'.format(account['name'], len(usageDataPoints)))
            influx.write_points(usageDataPoints)
        except:
            error('Failed to record new usage data: {}'.format(sys.exc_info())) 

    pauseEvent.wait(INTERVAL_SECS)

info('Finished')

