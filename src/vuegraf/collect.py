# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to collection of data usage from Emporia cloud

import datetime
from dataclasses import dataclass
import logging
import time
from typing import Union

from pyemvue.enums import Scale, Unit

from vuegraf.config import getConfigValue, getInfluxTag
from vuegraf.device import lookupDeviceName, lookupChannelName
from vuegraf.influx import getLastDBTimeStamp
from vuegraf.time import calculateHistoryTimeRange, convertToLocalDayInUTC


logger = logging.getLogger('vuegraf.data')


# How long to remember that a (device, channel) returned no minute-history data,
# before attempting the 7-day rewind backfill again. See getMinuteBackfillSkipCache().
MINUTE_BACKFILL_SKIP_TTL_SEC = 3600  # 1 hour


def getMinuteBackfillSkipCache(config):
    """Negative cache for the minute-history backfill loop in extractDataPoints.

    Some Emporia hardware revisions persistently return all-None from
    get_chart_usage(scale=MINUTE) for a device's synthesized parent channel
    (e.g. '1,2,3'), even though hourly/daily aggregates and the live-sample
    endpoint return real values for the same channel. Without a cache, when
    InfluxDB has no minute-tagged record for such a channel, getLastDBTimeStamp
    re-anchors at now - 7 days every cycle, and the backfill while-loop here
    walks the full 7-day window in 12-hour chunks — fetching nothing and
    writing nothing — on every single 60s cycle, indefinitely.

    This cache records '(deviceName, chanName) -> expiry_epoch' for channels
    where a backfill window completed without writing any points. Subsequent
    cycles short-circuit to the simple current-sample minute point (same path
    excluded channels already take) until the cache entry expires.

    The cache is bound to the config dict (one cache per Vuegraf process). It
    stays empty for channels that have data — zero overhead for working
    installations. Restarting Vuegraf clears it, giving the upstream API a
    fresh attempt.
    """
    return config.setdefault('_minuteBackfillSkipCache', {})


@dataclass
class Point:
    """Container for timestamped device readings from Vue.

    These can be repacked by Influx and MQTT when writing to those engines.
    """
    accountName: str
    deviceName: str  # aka station; the Vue, smart plug, etc
    chanName: str  # for example, each circuit or the total/balance
    usageWatts: float
    timestamp: datetime.datetime  # zone aware, UTC
    detailed: Union[bool, str]  # 'Minutes', 'Days', etc or False


def extractDataPoints(config, account, device, stopTimeUTC, collectDetails, usageDataPoints: list[Point],
                      detailedStartTimeUTC, pointType=None, historyStartTimeUTC=None, historyEndTimeUTC=None):
    """Unpacks Vue API usage data from a fetched device. Module use only.

    Modifies usageDataPoints in place, appending Point objects.
    """
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
                # Negative-cache check: if a prior backfill window for this
                # (device, channel) completed without writing any minute points,
                # short-circuit to the simple current-sample branch and skip the
                # 7-day-rewind getLastDBTimeStamp + while-loop entirely. See
                # getMinuteBackfillSkipCache for the rationale.
                skipCache = getMinuteBackfillSkipCache(config)
                cacheKey = (deviceName, chanName)
                skipExpiry = skipCache.get(cacheKey)
                if skipExpiry is not None and skipExpiry > time.time():
                    minuteHistoryStartTime, stopTimeMin, minuteHistoryEnabled = (stopTimeUTC, stopTimeUTC, False)
                else:
                    # Collect previous minute averages
                    minuteHistoryStartTime, stopTimeMin, minuteHistoryEnabled = getLastDBTimeStamp(config, deviceName,
                                                                                                   chanName, tagValue_minute,
                                                                                                   stopTimeUTC, stopTimeUTC, False)
                if not minuteHistoryEnabled or chanNum in excludedDetailChannelNumbers:
                    watts = float(minutesInAnHour * wattsInAKw) * kwhUsage
                    timestamp = stopTimeUTC.replace(second=0)
                    usageDataPoints.append(Point(accountName, deviceName, chanName, watts, timestamp, tagValue_minute))
                elif chanNum not in excludedDetailChannelNumbers and historyStartTimeUTC is None:
                    # Still missing recent minute history, attempt to collect in batches of 12 hours
                    pointsBeforeBackfill = len(usageDataPoints)
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
                            usageDataPoints.append(
                                Point(
                                    accountName, deviceName, chanName, watts,
                                    timestamp, tagValue_minute
                                )
                            )
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
                    if len(usageDataPoints) == pointsBeforeBackfill:
                        # The backfill window completed without writing any minute
                        # points -- the upstream API returned all-None across the
                        # entire 7-day rewind for this (device, channel). Cache the
                        # negative result so subsequent cycles skip the rewind.
                        skipCache[cacheKey] = time.time() + MINUTE_BACKFILL_SKIP_TTL_SEC
                        logger.info(
                            'No historical minute data for device="%s"; suppressing '
                            'minute backfill for %ds (cache).',
                            chanName, MINUTE_BACKFILL_SKIP_TTL_SEC,
                        )
            elif pointType == tagValue_day:
                # Collect previous day averages
                watts = kwhUsage * wattsInAKw
                timestamp = convertToLocalDayInUTC(config, historyStartTimeUTC)
                usageDataPoints.append(Point(accountName, deviceName, chanName, watts, timestamp, pointType))
            elif pointType == tagValue_hour:
                # Collect previous hour averages
                watts = kwhUsage * wattsInAKw
                timestamp = historyStartTimeUTC
                usageDataPoints.append(Point(accountName, deviceName, chanName, watts, timestamp, pointType))

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
                usageDataPoints.append(Point(accountName, deviceName, chanName, watts, timestamp, tagValue_second))
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
                usageDataPoints.append(Point(accountName, deviceName, chanName,
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
                usageDataPoints.append(Point(accountName, deviceName, chanName, watts, timestamp, tagValue_day))
                index += 1


def collectUsage(config, account, startTimeUTC, stopTimeUTC, collectDetails, usageDataPoints: list[Point], detailedStartTimeUTC, scale):
    """Module entrypoint. Fetch Vue data and unpack it into points.

    The usageDataPoints list is modified in place, appending Points.
    """
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


def collectHistoryUsage(config, account, startTimeUTC, stopTimeUTC, usageDataPoints: list[Point], pauseEvent):
    """Module entrypoint. Fetches historic Vue data and unpacks it into points."""
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
