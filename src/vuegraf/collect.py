# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to collection of data usage from Emporia cloud

import datetime
import logging
from pyemvue.enums import Scale, Unit

from vuegraf.config import getConfigValue, getInfluxTag
from vuegraf.device import lookupDeviceName, lookupChannelName
from vuegraf.influx import createDataPoint, getLastDBTimeStamp
from vuegraf.time import calculateHistoryTimeRange, convertToLocalDayInUTC


logger = logging.getLogger('vuegraf.data')


def extractDataPoints(config, account, device, stopTimeUTC, collectDetails, usageDataPoints,
                      detailedStartTimeUTC, pointType=None, historyStartTimeUTC=None, historyEndTimeUTC=None):
    accountName = account['name']
    detailedDataEnabled = getConfigValue(config, 'detailedDataEnabled')
    detailedSecondsEnabled = detailedDataEnabled and getConfigValue(config, 'detailedDataSecondsEnabled')
    _, tagValue_second, tagValue_minute, tagValue_hour, tagValue_day = getInfluxTag(config)
    excludedDetailChannelNumbers = ['Balance', 'TotalUsage']
    minutesInAnHour = 60
    secondsInAMinute = 60
    wattsInAKw = 1000
    deviceName = lookupDeviceName(account, device.device_gid)

    for chanNum, chan in device.channels.items():
        if chan.nested_devices:
            for gid, nestedDevice in chan.nested_devices.items():
                extractDataPoints(config, account, nestedDevice, stopTimeUTC, collectDetails, usageDataPoints,
                                  detailedStartTimeUTC, pointType, historyStartTimeUTC, historyEndTimeUTC)

        chanName = lookupChannelName(account, chan)
        kwhUsage = chan.usage
        if kwhUsage is not None:
            if pointType is None:
                # Collect previous minute averages
                minuteHistoryStartTime, stopTimeMin, minuteHistoryEnabled = getLastDBTimeStamp(config, deviceName,
                                                                                               chanName, tagValue_minute,
                                                                                               stopTimeUTC, stopTimeUTC, False)
                if not minuteHistoryEnabled or chanNum in excludedDetailChannelNumbers:
                    watts = float(minutesInAnHour * wattsInAKw) * kwhUsage
                    timestamp = stopTimeUTC.replace(second=0)
                    usageDataPoints.append(createDataPoint(config, accountName, deviceName, chanName, watts, timestamp, tagValue_minute))
                elif chanNum not in excludedDetailChannelNumbers and historyStartTimeUTC is None:
                    # Still missing recent minute history, attempt to collect in batches of 12 hours
                    noDataFlag = True
                    while noDataFlag:
                        # Collect minutes history (if neccessary, never during history collection)
                        logger.info('Get minute details; device="{}"; start="{}"; stop="{}"'.format(chanName,
                                    minuteHistoryStartTime, stopTimeMin))
                        usage, usage_start_time = account['vue'].get_chart_usage(chan, minuteHistoryStartTime, stopTimeMin,
                                                                                 scale=Scale.MINUTE.value, unit=Unit.KWH.value)
                        usage_start_time = usage_start_time.replace(second=0, microsecond=0)
                        index = 0
                        for kwhUsage in usage:
                            if kwhUsage is None:
                                index += 1
                                continue
                            noDataFlag = False  # Got at least one datapoint.  Set boolean value so we don't loop back
                            timestamp = usage_start_time + datetime.timedelta(minutes=index)
                            watts = float(minutesInAnHour * wattsInAKw) * kwhUsage
                            usageDataPoints.append(createDataPoint(config, accountName, deviceName, chanName, watts,
                                                                   timestamp, tagValue_minute))
                            index += 1
                        if noDataFlag:
                            # Opps!  No data points found for the time interval in question ('None' returned for ALL values)
                            # Move up the time interval to the next "batch" timeframe
                            if stopTimeMin < stopTimeUTC.replace(second=0, microsecond=0):
                                currentIntervalSeconds = int((stopTimeMin - minuteHistoryStartTime).total_seconds())
                                minuteHistoryStartTime = minuteHistoryStartTime + datetime.timedelta(seconds=currentIntervalSeconds)
                                # Make sure we don't go beyond the global stopTimeUTC
                                minuteHistoryStartTime = min(minuteHistoryStartTime, stopTimeUTC.replace(second=0, microsecond=0))
                                stopTimeMin = stopTimeMin + datetime.timedelta(seconds=currentIntervalSeconds)
                                # Make sure we don't go beyond the global stopTimeUTC
                                stopTimeMin = min(stopTimeMin, stopTimeUTC.replace(second=0, microsecond=0))
                            else:  # Time to break out of the loop; looks like the device in question is offline
                                noDataFlag = False
                        minuteHistoryEnabled = False
            elif pointType == tagValue_day:
                # Collect previous day averages
                watts = kwhUsage * wattsInAKw
                timestamp = convertToLocalDayInUTC(config, historyStartTimeUTC)
                usageDataPoints.append(createDataPoint(config, accountName, deviceName, chanName, watts, timestamp, pointType))
            elif pointType == tagValue_hour:
                # Collect previous hour averages
                watts = kwhUsage * wattsInAKw
                timestamp = historyStartTimeUTC
                usageDataPoints.append(createDataPoint(config, accountName, deviceName, chanName, watts, timestamp, pointType))

        if chanNum in excludedDetailChannelNumbers:
            continue

        if collectDetails and detailedSecondsEnabled and historyStartTimeUTC is None:
            # Collect seconds (once per hour, never during history collection)
            secHistoryStartTime, stopTimeSec, secondHistoryEnabled = getLastDBTimeStamp(config, deviceName, chanName, tagValue_second,
                                                                                        detailedStartTimeUTC, stopTimeUTC,
                                                                                        detailedSecondsEnabled)
            logger.debug('Get second details; device="{}"; start="{}"; stop="{}"'.format(chanName, secHistoryStartTime, stopTimeSec))
            usage, usageStartTimeUTC = account['vue'].get_chart_usage(chan, secHistoryStartTime, stopTimeSec, scale=Scale.SECOND.value,
                                                                      unit=Unit.KWH.value)
            usageStartTimeUTC = usageStartTimeUTC.replace(microsecond=0)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    index += 1
                    continue
                timestamp = usageStartTimeUTC + datetime.timedelta(seconds=index)
                watts = float(secondsInAMinute * minutesInAnHour * wattsInAKw) * kwhUsage
                usageDataPoints.append(createDataPoint(config, accountName, deviceName, chanName, watts, timestamp, tagValue_second))
                index += 1

        # Fetches historical Hour & Day data
        if historyStartTimeUTC is not None and historyEndTimeUTC is not None:
            logger.debug('Get historic details; device="{}"; start="{}"; stop="{}"'.format(chanName, historyStartTimeUTC,
                                                                                           historyEndTimeUTC))

            # Collect historical hour averages
            usage, usageStartTimeUTC = account['vue'].get_chart_usage(chan, historyStartTimeUTC, historyEndTimeUTC,
                                                                      scale=Scale.HOUR.value, unit=Unit.KWH.value)
            usageStartTimeUTC = usageStartTimeUTC.replace(minute=0, second=0, microsecond=0)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    index += 1
                    continue
                timestamp = usageStartTimeUTC + datetime.timedelta(hours=index)
                watts = kwhUsage * wattsInAKw
                usageDataPoints.append(createDataPoint(config, accountName, deviceName, chanName,
                                       watts, timestamp, tagValue_hour))
                index += 1

            # Collect historical day averages
            usage, usageStartTimeUTC = account['vue'].get_chart_usage(chan, historyStartTimeUTC, historyEndTimeUTC,
                                                                      scale=Scale.DAY.value, unit=Unit.KWH.value)
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    index += 1
                    continue

                # Advance the day by 6 hours + the current day index. The 6 hours shifts time away from common DST threshold hours
                # to avoid DST issues. Note that historyStartTimeUTC will be set to midnight by the caller, thus
                # usageStartTimeUTC, returned by Emporia, will be midnight as well.
                timestamp = convertToLocalDayInUTC(config, usageStartTimeUTC + datetime.timedelta(hours=6, days=index))

                watts = kwhUsage * wattsInAKw
                usageDataPoints.append(createDataPoint(config, accountName, deviceName, chanName, watts, timestamp, tagValue_day))
                index += 1


def collectUsage(config, account, startTimeUTC, stopTimeUTC, collectDetails, usageDataPoints, detailedStartTimeUTC, scale):
    _, _, _, tagValue_hour, tagValue_day = getInfluxTag(config)
    if scale == Scale.HOUR.value:
        pointType = tagValue_hour
    elif scale == Scale.DAY.value:
        pointType = tagValue_day
    else:
        pointType = None

    logger.debug('Collecting data from Emporia; Scale={}; startTimeUTC={}; stopTimeUTC={}'.format(scale, startTimeUTC, stopTimeUTC))

    deviceGids = list(account['deviceIdMap'].keys())
    usages = account['vue'].get_device_list_usage(deviceGids, stopTimeUTC, scale=scale, unit=Unit.KWH.value)
    if usages is not None:
        for gid, device in usages.items():
            extractDataPoints(config, account, device, stopTimeUTC, collectDetails,
                              usageDataPoints, detailedStartTimeUTC, pointType, startTimeUTC)


def collectHistoryUsage(config, account, startTimeUTC, stopTimeUTC, usageDataPoints, pauseEvent):
    # Grab base usage data for later use in history collection
    deviceGids = list(account['deviceIdMap'].keys())
    usages = account['vue'].get_device_list_usage(deviceGids, stopTimeUTC, scale=Scale.MINUTE.value, unit=Unit.KWH.value)

    historicBatchCounter = 0
    while True:
        # Determine history start and end times for this batch
        incrementStartTimeUTC, incrementEndTimeUTC = calculateHistoryTimeRange(config, stopTimeUTC, startTimeUTC,
                                                                               historicBatchCounter)
        if incrementStartTimeUTC >= stopTimeUTC:
            break

        # Collect usage data for the historical period
        logger.debug('Collecting history data from Emporia; incrementStartTimeUTC={}; incrementEndTimeUTC={}'.format(
                     incrementStartTimeUTC, incrementEndTimeUTC))
        for gid, device in usages.items():
            extractDataPoints(config, account, device, stopTimeUTC, False, usageDataPoints, None,
                              'History', incrementStartTimeUTC, incrementEndTimeUTC)

        historicBatchCounter = historicBatchCounter + 1

        if pauseEvent.wait(5):
            logging.info("Aborting history collection due to pause interruption")
            break
