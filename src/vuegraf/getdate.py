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

    query1 = 'select last(usage) from "energy_usage" where "detailed"=\'Hour\''
    query2 = 'select last(usage) from "energy_usage" where "detailed"=\'Day\''
    query3 = 'select last(usage) from "energy_usage" where "detailed"=\'False\''
    print(query1)
    last = influx.query(query1)
    print(last)
    last = influx.query(query2)
    print(last)
    last = influx.query(query3)
    print(last)
    
    for record in 




except:
    error('Fatal error: {}'.format(sys.exc_info())) 
    traceback.print_exc()


