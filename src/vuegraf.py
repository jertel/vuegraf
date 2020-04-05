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
    print('Resetting database')
    influx.delete_series(measurement='energy_usage')

running = True

def handleExit(signum, frame):
    global running
    print('Caught exit signal')
    running = False
    pauseEvent.set()

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
            print('Login completed')

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
            deviceIndex = 0
            chanIndex = 0
            device = None
            for chan in channels:
                chanName = '{}-{}'.format(chan.device_gid, chan.channel_num)

                # Attempt to relabel this channel, if the config has user-friendly names defined
                if 'devices' in account:
                    if chan.channel_num == '1,2,3':
                        if len(account['devices']) > deviceIndex:
                            device = account['devices'][deviceIndex]
                            if 'name' in device:
                                chanName = device['name']
                        deviceIndex = deviceIndex + 1
                        chanIndex = 0
                    elif device is not None:
                        if len(device['channels']) > chanIndex:
                            chanName = device['channels'][chanIndex]
                            chanIndex = chanIndex + 1

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

            print('Submitted datapoints to database; account="{}"; points={}'.format(account['name'], len(usageDataPoints)))
            influx.write_points(usageDataPoints)
        except:
            print('Failed to record new usage data', sys.exc_info()) 

    pauseEvent.wait(INTERVAL_SECS)

print('Finished')