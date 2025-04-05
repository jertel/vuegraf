# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to preparing, retrieving and saving InfluxDB data points.

import datetime
import influxdb         # InfluxDB v1
import influxdb_client  # InfluxDB v2
import logging
import pprint

from vuegraf.config import getConfigValue, getInfluxTag, getInfluxVersion
from vuegraf.time import getTimeNow


logger = logging.getLogger('vuegraf.influx')


def createDataPoint(config, accountName, deviceName, chanName, watts, timestamp, detailed):
    influxVersion = getInfluxVersion(config)
    tagName, tagValue_second, tagValue_minute, tagValue_hour, tagValue_day = getInfluxTag(config)
    addStationField = getConfigValue(config, 'addStationField')

    dataPoint = None
    if influxVersion == 2:
        dataPoint = influxdb_client.Point('energy_usage')
        dataPoint.tag('account_name', accountName)
        dataPoint.tag('device_name', chanName)
        dataPoint.tag(tagName, detailed)
        dataPoint.field('usage', watts)
        dataPoint.time(time=timestamp)
        if addStationField:
            dataPoint.tag('station_name', deviceName)
    else:
        dataPoint = {
            'measurement': 'energy_usage',
            'tags': {
                'account_name': accountName,
                'device_name': chanName,
                tagName: detailed,
            },
            'fields': {
                'usage': watts,
            },
            'time': timestamp
        }
        if addStationField:
            dataPoint['tags']['station_name'] = deviceName

    return dataPoint


def getLastDBTimeStamp(config, deviceName, chanName, pointType, startTime, stopTime, fillInMissingData):
    tagName, tagValue_second, tagValue_minute, tagValue_hour, tagValue_day = getInfluxTag(config)
    influxVersion = getInfluxVersion(config)
    addStationField = getConfigValue(config, 'addStationField')
    timeStr = ''
    # Get timestamp of last record in database
    # Influx v2
    if influxVersion == 2:
        stationFilter = ""
        if addStationField:
            stationFilter = '  r.station_name == "' + deviceName + '" and '
        bucket = config['influxDb']['bucket']
        query_api = config['influx'].query_api()
        result = query_api.query('from(bucket:"' + bucket + '") ' +
                                 '|> range(start: -3w) ' +
                                 '|> filter(fn: (r) => ' +
                                 '  r._measurement == "energy_usage" and ' +
                                 '  r.' + tagName + ' == "' + pointType + '" and ' +
                                 '  r._field == "usage" and ' + stationFilter +
                                 '  r.device_name == "' + chanName + '")' +
                                 '|> last()')

        if len(result) > 0 and len(result[0].records) > 0:
            lastRecord = result[0].records[0]
            timeStr = lastRecord['_time'].isoformat()

    else:  # Influx v1
        stationFilter = ""
        if addStationField:
            stationFilter = 'station_name = \'' + deviceName.replace('\'', '\\\'') + '\' AND '
        query = 'select last(usage), time from energy_usage where (' + stationFilter + \
                'device_name = \'' + chanName.replace('\'', '\\\'') + '\' AND ' + \
                tagName.replace('\'', '\\\'') + ' = \'' + pointType + '\')'
        logger.debug('InfluxDB v1 Query: %s', query)
        result = config['influx'].query(query)

        if len(result) > 0:
            timeStr = next(result.get_points())['time']

    # Depending on version of Influx, the string format for the time is different.
    # So strip out the variable timezone bits (along with any microsecond values)
    if len(timeStr) > 0:
        timeStr = timeStr[:19] + 'Z'

        # Convert the timeStr into an aware datetime object.
        dbLastRecordTime = datetime.datetime.strptime(timeStr, '%Y-%m-%dT%H:%M:%S%z').replace(tzinfo=datetime.timezone.utc)

        if pointType == tagValue_minute:
            if dbLastRecordTime < (stopTime - datetime.timedelta(minutes=2, seconds=stopTime.second)):
                fillInMissingData = True
                startTime = dbLastRecordTime + datetime.timedelta(minutes=1)
                # Can only back a maximum of 7 days for minute data.
                # So if last record in DB exceeds 7 days, set the startTime to be 7 days ago.
                if int((stopTime - startTime).total_seconds()) > 604800:      # 7 Days
                    startTime = stopTime - datetime.timedelta(minutes=10080)  # 7 Days

                # Can only get a maximum of 12 hours worth of minute data in a single API call.
                # If more than 12 hours worth is needed, get data in batches; set stopTime to be
                # 12 hours more than the starttime
                if int((stopTime - startTime).total_seconds()) > 43200:       # 12 Hours
                    stopTime = startTime + datetime.timedelta(minutes=720)    # 12 Hours

        if pointType == tagValue_second:
            if dbLastRecordTime < (startTime - datetime.timedelta(seconds=2)):
                fillInMissingData = True
                startTime = (dbLastRecordTime + datetime.timedelta(seconds=1)).replace(microsecond=0)
                # Adjust start or stop times if backfill interval exceeds 1 hour
                if (int((stopTime - startTime).total_seconds()) > 3600):
                    detailedIntervalSecs = getConfigValue(config, 'detailedIntervalSecs')
                    # Can never get more than 1 hour of historical second data if detailedIntervalSecs
                    # is set to greater than 1h.  Set backfill period to be just the past one hour in that case.
                    if (detailedIntervalSecs > 3600):
                        # 1 Hour max since detailedIntervalSecs is more than 1 hour
                        startTime = stopTime - datetime.timedelta(seconds=3600)
                    else:
                        # Can only backfill a maximum of 3 hours for second data.
                        # So if last record in DB exceeds 3 hours, set the startTime to be 3 hours ago.
                        if int((stopTime - startTime).total_seconds()) > 10800:        # 3 Hours
                            startTime = stopTime - datetime.timedelta(seconds=10800)   # 3 Hours

                        # Can only get a maximum of 1 hour's worth of second data in a single API call.
                        # If more than 1 hour's worth is needed, get data in batches; set stopTime to be
                        # 1 hour more than the starttime
                        stopTime = startTime + datetime.timedelta(seconds=3600)  # limit to 1 hour batch
    else:
        if pointType == tagValue_minute:
            startTime = startTime - datetime.timedelta(days=7)
            stopTime = startTime + datetime.timedelta(hours=12)
            fillInMissingData = True
        elif pointType == tagValue_second:
            startTime = startTime - datetime.timedelta(hours=3)
            stopTime = startTime + datetime.timedelta(hours=1)
            fillInMissingData = True

    return startTime, stopTime, fillInMissingData


def initInfluxConnection(config):
    sslVerify = True
    if 'ssl_verify' in config['influxDb']:
        sslVerify = config['influxDb']['ssl_verify']

    influxVersion = getInfluxVersion(config)
    if influxVersion == 2:
        logger.info('Using InfluxDB version 2')
        bucket = config['influxDb']['bucket']
        org = config['influxDb']['org']
        token = config['influxDb']['token']
        url = config['influxDb']['url']
        influx = influxdb_client.InfluxDBClient(
           url=url,
           token=token,
           org=org,
           verify_ssl=sslVerify
        )

        if config['args'].resetdatabase:
            logger.info('Resetting database')
            delete_api = influx.delete_api()
            start = '1970-01-01T00:00:00Z'
            now = getTimeNow(datetime.UTC)
            stop = now.isoformat(timespec='seconds').replace("+00:00", "") + 'Z'
            delete_api.delete(start, stop, '_measurement="energy_usage"', bucket=bucket, org=org)

    else:
        logger.info('Using InfluxDB version 1')

        sslEnable = False
        if 'ssl_enable' in config['influxDb']:
            sslEnable = config['influxDb']['ssl_enable']

        # Only authenticate to ingress if 'user' entry was provided in config
        if 'user' in config['influxDb']:
            influx = influxdb.InfluxDBClient(host=config['influxDb']['host'], port=config['influxDb']['port'],
                                             username=config['influxDb']['user'], password=config['influxDb']['pass'],
                                             database=config['influxDb']['database'], ssl=sslEnable, verify_ssl=sslVerify)
        else:
            influx = influxdb.InfluxDBClient(host=config['influxDb']['host'], port=config['influxDb']['port'],
                                             database=config['influxDb']['database'], ssl=sslEnable, verify_ssl=sslVerify)

        influx.create_database(config['influxDb']['database'])

        if config['args'].resetdatabase:
            logger.info('Resetting database')
            influx.delete_series(measurement='energy_usage')

    config['influx'] = influx


def writeInfluxPoints(config, usageDataPoints):
    # Write to database after each historical batch to prevent timeout issues on large history intervals.
    logger.info('Submitting datapoints to database; points={}'.format(len(usageDataPoints)))
    if config['args'].debug:
        dumpPoints(config, "Sending to database", usageDataPoints)
    if config['args'].dryrun:
        logger.info('Dryrun mode enabled.  Skipping database write.')
    else:
        influxVersion = getInfluxVersion(config)
        if influxVersion == 2:
            bucket = config['influxDb']['bucket']
            write_api = config['influx'].write_api(write_options=influxdb_client.client.write_api.SYNCHRONOUS)
            write_api.write(bucket=bucket, record=usageDataPoints)
        else:
            config['influx'].write_points(usageDataPoints, batch_size=5000)


def dumpPoints(config, label, usageDataPoints):
    influxVersion = getInfluxVersion(config)
    logger.debug(label)
    for point in usageDataPoints:
        if influxVersion == 2:
            logger.debug('  {}'.format(point.to_line_protocol()))
        else:
            logger.debug(f'  {pprint.pformat(point)}')
