import datetime
import json
import signal
import sys
import time
from threading import Event
from influxdb import InfluxDBClient
from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit, TotalTimeFrame, TotalUnit

if len(sys.argv) != 2:
    print('Usage: python {} <config-file>'.format(sys.argv[0]))
    sys.exit(1)

configFilename = sys.argv[1]
config = {}
with open(configFilename) as configFile:
    config = json.load(configFile)

influx = InfluxDBClient(config['influxDb']['host'], config['influxDb']['port'], config['influxDb']['user'], config['influxDb']['pass'], config['influxDb']['database'])
influx.create_database(config['influxDb']['database'])
if config['influxDb']['reset']:
    info('Resetting database')
    influx.delete_series(measurement='energy_usage')

running = True

def log(level, msg):
    now = datetime.datetime.utcnow()
    print('{} | {} | {}'.format(now, level.ljust(5), msg))

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
            channels = account['vue'].get_recent_usage(Scale.MINUTE.value)
            usageDataPoints = []
            device = None
            for chan in channels:
                chanName = lookupChannelName(account, chan)

                usage = account['vue'].get_usage_over_time(chan, start, account['end'])
                index = 0
                for watts in usage:
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
            error('Failed to record new usage data', sys.exc_info()) 

    pauseEvent.wait(INTERVAL_SECS)

info('Finished')