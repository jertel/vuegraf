# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to collection of data usage from Emporia cloud

import datetime
from dataclasses import dataclass
import logging
import time
from typing import Union

from pyemvue.enums import Scale, Unit

from vuegraf.cache import (
    bootstrapManifestFromCsvs,
    getCacheCsvPath,
    isCacheEnabled,
    loadManifest,
    loadMirroredKeys,
    mergeCoverage,
    restoreCacheToDatabase,
    saveManifest,
    subtractCoverage,
    mirrorPoints,
)
from vuegraf.config import getConfigValue
from vuegraf.device import lookupDeviceName, lookupChannelName
from vuegraf.destination import getLastDBTimeStamp, getTags, writeDataPoints
from vuegraf.hierarchy import applyBalances, registerImplicitEdge
from vuegraf.time import calculateHistoryTimeRange, convertToLocalDayInUTC, getLocalDayStartUTC


logger = logging.getLogger('vuegraf.data')


# How long to remember that a (device, channel) returned no minute-history data,
# before attempting the 7-day rewind backfill again. See getMinuteBackfillSkipCache().
MINUTE_BACKFILL_SKIP_TTL_SEC = 3600  # 1 hour

# How long to stay quiet about a series that keeps reporting a negative reading. Like a
# negative net balance, a mis-scaled channel is a standing condition, not a transient one.
NEGATIVE_USAGE_WARN_TTL_SEC = 3600  # 1 hour

# How far past a requested stop a batch's points can still land, used to bound the
# de-duplication window in fetchHistoryRange. The Day tier stamps its point at the end of
# the local day, so it can sit up to a day plus the UTC offset after the stop; two days
# clears that for every timezone.
MIRRORED_KEY_MARGIN = datetime.timedelta(days=2)


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


def clampNegativeWatts(config, usageDataPoints, startIndex=0):
    """Clamps negative readings to zero, when 'clampNegativeUsage' is enabled.

    Emporia's derived channels go negative when the parts read higher than the whole:
    'Balance' is the device total minus its circuits, so a single mis-scaled channel (a
    120V circuit configured as 240V, say) or ordinary CT tolerance drives it below zero.
    That is a measurement fault rather than energy flowing backwards, and leaving it in
    the database distorts every sum and graph built on the series.

    This is the same treatment the hierarchy already gives its own Net Balance: clamp to
    zero, and warn once per series per hour so a standing fault stays visible without
    filling the log. Applied before the balance pass, so balances are computed from the
    clamped values.

    Off by default, because a negative reading is legitimate on a solar or battery
    install, where it means export. Enable it only when nothing in the system can produce
    power, so a negative value can only be an error. Returns usageDataPoints.
    """
    if not getConfigValue(config, 'clampNegativeUsage'):
        return usageDataPoints

    expiries = config.setdefault('_negativeUsageWarnExpiry', {})
    now = time.time()
    clamped = 0
    suppressed = 0
    for point in usageDataPoints[startIndex:]:
        if point.usageWatts >= 0.0:
            continue
        clamped += 1
        key = (point.deviceName, point.chanName)
        if expiries.get(key, 0.0) > now:
            suppressed += 1
        else:
            expiries[key] = now + NEGATIVE_USAGE_WARN_TTL_SEC
            logger.warning('Clamping negative usage to zero; device="{}"; channel="{}"; watts={:.1f}; the channels '
                           'are measuring more than the device total, so check that channel for a wrong circuit '
                           'type or multiplier'.format(point.deviceName, point.chanName, point.usageWatts))
        point.usageWatts = 0.0
    if suppressed > 0:
        logger.info('Clamped {} negative readings; suppressed {} repeat warnings (at most one per series per {}s)'.format(
                    clamped, suppressed, NEGATIVE_USAGE_WARN_TTL_SEC))
    return usageDataPoints


def nestedDeviceUsage(nestedDevice):
    """The usage a nested device reports for itself, from its mains ('1,2,3') channel.

    That channel is the device's total, so it is what a parent channel had deducted from
    it. Missing or unreported usage counts as zero. See the note in extractDataPoints.
    """
    total = 0.0
    for chanNum, chan in nestedDevice.channels.items():
        if chanNum == '1,2,3' and chan.usage is not None:
            total += chan.usage
    return total


def historySeriesByTimestamp(config, account, channel, startUTC, endUTC, scale):
    """{timestamp: kwh} for one channel over a history window.

    Timestamps are derived exactly as extractDataPoints stamps its own history points,
    so a caller can combine two channels' series by timestamp rather than by index --
    the two need not start at the same bucket, and a None reading leaves a hole.
    """
    usage, startedUTC = account['vue'].get_chart_usage(channel, startUTC, endUTC, scale=scale, unit=Unit.KWH.value)
    series = {}
    if scale == Scale.HOUR.value:
        startedUTC = startedUTC.replace(minute=0, second=0, microsecond=0)
    for index, kwhUsage in enumerate(usage or []):
        if kwhUsage is None:
            continue
        if scale == Scale.HOUR.value:
            timestamp = startedUTC + datetime.timedelta(hours=index)
        else:
            timestamp = convertToLocalDayInUTC(config, startedUTC + datetime.timedelta(hours=6, days=index))
        series[timestamp] = kwhUsage
    return series


def nestedHistoryTotals(config, account, chan, startUTC, endUTC, scale):
    """Per-bucket total of the mains of every device nested under this channel.

    Emporia deducts a nested device's usage from the reading of whatever it hangs
    beneath, and the live path adds it back (see nestedDeviceUsage) so a series means
    the same thing whoever collected it. get_chart_usage needs the same treatment, but
    only for a device's mains ('1,2,3'): its numbered circuits already include the
    nested load, so adding it there too would double count. That asymmetry is observed
    behaviour rather than something Emporia documents, which is why it is applied
    narrowly. Without it a backfilled reading sits below a live one for any panel with
    a device nested beneath it.

    Returns {} when nothing is nested, which is the common case and costs no API calls.
    """
    totals = {}
    for gid, nestedDevice in (chan.nested_devices or {}).items():
        for nestedChanNum, nestedChan in nestedDevice.channels.items():
            if nestedChanNum != '1,2,3':
                continue
            for timestamp, kwhUsage in historySeriesByTimestamp(
                    config, account, nestedChan, startUTC, endUTC, scale).items():
                totals[timestamp] = totals.get(timestamp, 0.0) + kwhUsage
    return totals


def extractDataPoints(config, account, device, stopTimeUTC, collectDetails, usageDataPoints: list[Point],
                      detailedStartTimeUTC, pointType=None, historyStartTimeUTC=None, historyEndTimeUTC=None):
    """Unpacks Vue API usage data from a fetched device. Module use only.

    Modifies usageDataPoints in place, appending Point objects.
    """
    accountName = account['name']
    detailedDataEnabled = getConfigValue(config, 'detailedDataEnabled')
    detailedSecondsEnabled = detailedDataEnabled and getConfigValue(config, 'detailedDataSecondsEnabled')
    _, tagValue_second, tagValue_minute, tagValue_hour, tagValue_day = getTags(config)
    excludedDetailChannelNumbers = ['Balance', 'TotalUsage']
    minutesInAnHour = 60
    secondsInAMinute = 60
    wattsInAKw = 1000
    deviceName = lookupDeviceName(account, device.device_gid)

    for chanNum, chan in device.channels.items():
        nestedUsageKwh = 0.0
        if chan.nested_devices:
            # Emporia reports its own nesting only on usage responses, so this is where the
            # implicit edges become visible. Registering them here folds them into the same
            # tree as the config-declared ones. No-op unless the account opted in.
            nestedParentChanName = lookupChannelName(account, chan)
            for gid, nestedDevice in chan.nested_devices.items():
                registerImplicitEdge(config, accountName, lookupDeviceName(account, gid), nestedParentChanName)
                nestedUsageKwh += nestedDeviceUsage(nestedDevice)
                extractDataPoints(config, account, nestedDevice, stopTimeUTC, collectDetails, usageDataPoints,
                                  detailedStartTimeUTC, pointType, historyStartTimeUTC, historyEndTimeUTC)

        chanName = lookupChannelName(account, chan)
        kwhUsage = chan.usage
        if kwhUsage is not None:
            # Restore this channel to its whole load. get_device_list_usage reports a
            # channel NET of any device nested beneath it -- a plug or monitor assigned to
            # this circuit in the app -- and declares which ones via chan.nested_devices;
            # it is an allocation view, built so a breakdown does not double count. Taking
            # that value as the whole circuit is what makes the balance pass subtract the
            # same children a second time, leaving a parent short by the entire child.
            #
            # Restoring the total here rather than skipping the subtraction downstream also
            # keeps one meaning for the series: get_chart_usage, which the history backfill
            # uses, reports the raw CT reading, so otherwise a backfilled hour and a live
            # hour would hold different measurements. It preserves the hierarchy invariant
            # too -- device mains are never deducted, so an exclusive circuit would leave
            # the nested load in the parent's remainder AND in the child's own series.
            #
            # Caveat: the deduction is floored at zero, so when a nested device measures
            # more than its parent channel the original total cannot be recovered exactly;
            # the reconstruction is then the sum of the children, which is the best lower
            # bound available and errs toward a zero rather than a negative balance.
            kwhUsage += nestedUsageKwh
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
            nestedHourly = (nestedHistoryTotals(config, account, chan, historyStartTimeUTC, historyEndTimeUTC,
                                                Scale.HOUR.value) if chanNum == '1,2,3' else {})
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    index += 1
                    continue
                timestamp = usageStartTimeUTC + datetime.timedelta(hours=index)
                watts = (kwhUsage + nestedHourly.get(timestamp, 0.0)) * wattsInAKw
                usageDataPoints.append(Point(accountName, deviceName, chanName,
                                       watts, timestamp, tagValue_hour))
                index += 1

            # Collect historical day averages
            usage, usageStartTimeUTC = account['vue'].get_chart_usage(chan, historyStartTimeUTC, historyEndTimeUTC,
                                                                      scale=Scale.DAY.value, unit=Unit.KWH.value)
            nestedDaily = (nestedHistoryTotals(config, account, chan, historyStartTimeUTC, historyEndTimeUTC,
                                               Scale.DAY.value) if chanNum == '1,2,3' else {})
            index = 0
            for kwhUsage in usage:
                if kwhUsage is None:
                    index += 1
                    continue

                # Advance the day by 6 hours + the current day index. The 6 hours shifts time away from common DST threshold hours
                # to avoid DST issues. Note that historyStartTimeUTC will be set to midnight by the caller, thus
                # usageStartTimeUTC, returned by Emporia, will be midnight as well.
                timestamp = convertToLocalDayInUTC(config, usageStartTimeUTC + datetime.timedelta(hours=6, days=index))

                watts = (kwhUsage + nestedDaily.get(timestamp, 0.0)) * wattsInAKw
                usageDataPoints.append(Point(accountName, deviceName, chanName, watts, timestamp, tagValue_day))
                index += 1


def collectUsage(config, account, startTimeUTC, stopTimeUTC, collectDetails, usageDataPoints: list[Point], detailedStartTimeUTC, scale):
    """Module entrypoint. Fetch Vue data and unpack it into points.

    The usageDataPoints list is modified in place, appending Points.
    """
    _, _, _, tagValue_hour, tagValue_day = getTags(config)
    if scale == Scale.HOUR.value:
        pointType = tagValue_hour
    elif scale == Scale.DAY.value:
        pointType = tagValue_day
    else:
        pointType = None

    logger.debug('Collecting data from Emporia; Scale={}; startTimeUTC={}; stopTimeUTC={}'.format(scale, startTimeUTC, stopTimeUTC))

    deviceGids = list(account['deviceIdMap'].keys())
    usages = account['vue'].get_device_list_usage(deviceGids, stopTimeUTC, scale=scale, unit=Unit.KWH.value)
    startIndex = len(usageDataPoints)
    if usages is not None:
        for gid, device in usages.items():
            extractDataPoints(config, account, device, stopTimeUTC, collectDetails,
                              usageDataPoints, detailedStartTimeUTC, pointType, startTimeUTC)
    clampNegativeWatts(config, usageDataPoints, startIndex)


def writeHistoryBatchCsv(config, account, points: list[Point], mirroredKeys=frozenset()):
    """Appends a batch of history points to the CSV cache, for recovery/inspection.

    Delegates to cache.mirrorPoints, the one gate every mirrored point passes
    through: still-open periods are held back until they settle, and readings the
    file already holds are not appended again. A no-op when the cache is disabled,
    in which case history goes only to the database.
    """
    mirrorPoints(config, account, points, mirroredKeys)


def fetchHistoryRange(config, account, usages, rangeStartUTC, rangeEndUTC, csvPath, usageDataPoints, pauseEvent,
                      mirroredSinceUTC=None):
    """Fetch history from the Emporia API for a single [start, stop] range.

    Batches the range into <=20-day increments, persisting each batch to the CSV
    cache (first, so a failed database write still leaves a recoverable file) and
    to the database, then clearing it from memory. usageDataPoints is reused as the
    per-batch buffer and is left empty on return.

    Returns (pointsWritten, earliestDataUTC, lastCompletedStopUTC, aborted) where
    lastCompletedStopUTC is how far coverage may be safely advanced (== the last
    batch's stop, or rangeStartUTC if nothing completed).
    """
    totalDays = max(1, (rangeEndUTC - rangeStartUTC).days)
    totalBatches = max(1, -(-totalDays // 20))  # ceil; matches 20-day increments in calculateHistoryTimeRange
    logger.info('Fetching uncovered history from API; start={}; stop={}; batches={}'.format(
                rangeStartUTC, rangeEndUTC, totalBatches))

    pointsWritten = 0
    earliestDataUTC = None
    lastCompletedStopUTC = rangeStartUTC
    aborted = False
    batchCounter = 0
    mirroredKeys = frozenset()
    mirroredKeysLoaded = False
    while True:
        # Anchor the 20-day windows at rangeStartUTC and clamp the stop to rangeEndUTC.
        incrementStartTimeUTC, incrementEndTimeUTC = calculateHistoryTimeRange(config, rangeEndUTC, rangeStartUTC,
                                                                               batchCounter)
        if incrementStartTimeUTC >= rangeEndUTC:
            break

        logger.info('History batch {}/{}; start={}; stop={}'.format(
                    batchCounter + 1, totalBatches, incrementStartTimeUTC, incrementEndTimeUTC))

        for gid, device in usages.items():
            extractDataPoints(config, account, device, rangeEndUTC, False, usageDataPoints, None,
                              'History', incrementStartTimeUTC, incrementEndTimeUTC)

        # A batch already holds every device for its window, so the balance pass runs per
        # batch with no cross-batch state. Appending here, before the writes below, is what
        # gets the Net Balance series into both the CSV mirror and InfluxDB. Clamping runs
        # first so the balances are netted from the clamped readings.
        clampNegativeWatts(config, usageDataPoints)
        applyBalances(config, usageDataPoints)

        if usageDataPoints:
            batchEarliest = min(pt.timestamp for pt in usageDataPoints)
            if earliestDataUTC is None or batchEarliest < earliestDataUTC:
                earliestDataUTC = batchEarliest

            if not mirroredKeysLoaded and mirroredSinceUTC is not None:
                # Lower bound: the earliest instant this batch ACTUALLY returned, not a
                # predicted boundary. calculateHistoryTimeRange already floors the request
                # to local midnight, and a returned point can be older still -- the Day
                # tier timestamps itself from Emporia's own bucket start (see
                # extractDataPoints), which begins before our start when the account's
                # timezone differs from the configured one, yielding the PREVIOUS day's
                # summary. Anchoring on data covers those without predicting them.
                #
                # Upper bound: the requested stop plus a margin for that same end-of-local-day
                # stamping. Without it a 'head' gap -- one reaching back past the current
                # coverage -- pairs its own old start with an open end and pulls the whole file
                # into memory to guard rows that lie outside the gap anyway.
                mirroredKeys = loadMirroredKeys(config, account, min(mirroredSinceUTC, batchEarliest),
                                                rangeEndUTC + MIRRORED_KEY_MARGIN)
                mirroredKeysLoaded = True

        batchPoints = len(usageDataPoints)
        pointsWritten += batchPoints
        writeHistoryBatchCsv(config, account, usageDataPoints, mirroredKeys)
        writeDataPoints(config, usageDataPoints)
        del usageDataPoints[:]
        lastCompletedStopUTC = incrementEndTimeUTC
        logger.info('History batch {}/{} complete; batchPoints={}; totalPoints={}'.format(
                    batchCounter + 1, totalBatches, batchPoints, pointsWritten))

        batchCounter = batchCounter + 1

        if pauseEvent.wait(5):
            logging.info("Aborting history collection due to pause interruption")
            aborted = True
            break

    return pointsWritten, earliestDataUTC, lastCompletedStopUTC, aborted


def collectHistoryUsage(config, account, startTimeUTC, stopTimeUTC, usageDataPoints: list[Point], pauseEvent):
    """Module entrypoint. Populates the database with historic Vue data.

    When the CSV cache is disabled (the default) the requested range is simply
    fetched from the Emporia API in batches and written to the database.

    When 'csvCacheEnabled' is set, the backfill CSV files act as a passthrough
    cache in front of the Emporia history API (see vuegraf.cache):
      1. Any part of the requested range already probed (per the cache manifest)
         is restored straight from CSV into the database -- no API calls. Restore can
         be skipped with --skiprestore when the database is known to be populated.
      2. Only the uncovered portion(s) of the requested range are fetched from
         the API (the "true-up"), appended to the cache, and written to the database.
      3. The manifest coverage is extended to include everything probed --
         including windows that returned no data -- so future runs never re-probe
         the empty pre-install era, regardless of the --historydays range used.
    """
    # Grab base usage data for later use in history collection
    deviceGids = list(account['deviceIdMap'].keys())
    usages = account['vue'].get_device_list_usage(deviceGids, stopTimeUTC, scale=Scale.MINUTE.value, unit=Unit.KWH.value)

    cacheEnabled = isCacheEnabled(config)

    # Resolve current cache coverage (bootstrapping from existing CSVs the first time).
    manifest = None
    if cacheEnabled:
        manifest = loadManifest(config, account)
    if cacheEnabled and manifest is None:
        bootstrapped = bootstrapManifestFromCsvs(config, account)
        if bootstrapped is not None:
            covStart, covEnd, earliestData = bootstrapped
            manifest = {'coveredStartUTC': covStart, 'coveredEndUTC': covEnd, 'earliestDataUTC': earliestData}
            logger.info('Bootstrapped history cache from existing CSVs; data={}..{}'.format(earliestData, covEnd))

    covStartUTC = manifest['coveredStartUTC'] if manifest else None
    covEndUTC = manifest['coveredEndUTC'] if manifest else None
    earliestDataUTC = manifest['earliestDataUTC'] if manifest else None

    logger.info('History collection started; requested={}..{}; cachedCoverage={}..{}'.format(
                startTimeUTC, stopTimeUTC, covStartUTC, covEndUTC))

    # 1) Serve the already-cached portion of the request from CSV into the database (no API calls).
    skipRestore = getattr(config.get('args'), 'skiprestore', False)
    if manifest and not skipRestore:
        restoreStartUTC = max(startTimeUTC, covStartUTC)
        restoreStopUTC = min(stopTimeUTC, covEndUTC)
        if restoreStartUTC < restoreStopUTC:
            logger.info('Restoring cached history from CSV; start={}; stop={}'.format(restoreStartUTC, restoreStopUTC))
            restored = restoreCacheToDatabase(config, account, restoreStartUTC, restoreStopUTC)
            logger.info('Restored {} cached points from CSV (no API calls)'.format(restored))
    elif manifest and skipRestore:
        logger.info('Skipping CSV restore (--skiprestore); cached range assumed already present in the database')

    # 2) Fetch only the uncovered portion(s) from the API and append them to the cache.
    gaps = subtractCoverage(startTimeUTC, stopTimeUTC, covStartUTC, covEndUTC)
    if not gaps:
        logger.info('History cache fully covers the requested range; no API calls needed')

    csvPath = getCacheCsvPath(config, account) if cacheEnabled else None
    coveredPieces = []
    if covStartUTC is not None and covEndUTC is not None:
        coveredPieces.append((covStartUTC, covEndUTC))

    # Coverage end in effect before this backfill; fetchHistoryRange uses it to skip
    # re-appending readings the running daemon already mirrored into the shared file.
    mirroredSinceUTC = covEndUTC if cacheEnabled else None

    for gapStartUTC, gapEndUTC in gaps:
        _, dataMinUTC, lastStopUTC, aborted = fetchHistoryRange(
            config, account, usages, gapStartUTC, gapEndUTC, csvPath, usageDataPoints, pauseEvent, mirroredSinceUTC)
        if dataMinUTC is not None and (earliestDataUTC is None or dataMinUTC < earliestDataUTC):
            earliestDataUTC = dataMinUTC
        # Only claim coverage for what actually completed (matters if aborted mid-gap), and
        # never past the last closed period: the mirror holds open periods back, so claiming
        # them would mean their final values are never collected. Existing coverage is a
        # separate piece and is never reduced by this.
        if cacheEnabled:
            lastStopUTC = min(lastStopUTC, getLocalDayStartUTC(config, gapEndUTC))
        if lastStopUTC > gapStartUTC:
            coveredPieces.append((gapStartUTC, lastStopUTC))
        if aborted:
            break

    # 3) Persist the merged coverage. Empty (probed-but-no-data) windows are included, so
    #    they are never re-probed on future runs.
    #
    #    The pieces are contiguous whenever every gap ran to completion, but an abort part
    #    way through the older 'head' gap leaves a hole between where it stopped and the
    #    start of the previously covered window. The manifest holds one window, so taking
    #    [min start, max stop] there would claim that hole as probed and never fetch it
    #    again. Instead the pieces are merged and only the window that actually contains
    #    the coverage we started from is kept; a piece cut off before it reached that
    #    window is dropped and re-fetched next run (its rows stay in the CSV, and Influx
    #    writes are idempotent, so nothing is lost by re-probing it).
    if cacheEnabled and coveredPieces:
        merged = mergeCoverage(coveredPieces)
        if covStartUTC is None:
            newCovStartUTC, newCovEndUTC = max(merged, key=lambda piece: piece[1] - piece[0])
        else:
            # The pre-existing coverage is one of the pieces, so some merged window contains it.
            newCovStartUTC, newCovEndUTC = [p for p in merged if p[0] <= covStartUTC <= p[1]][0]
        if len(merged) > 1:
            logger.warning('History collection ended with non-contiguous coverage; claiming {}..{} only. '
                           'The unclaimed interval(s) will be re-fetched on the next run.'.format(
                               newCovStartUTC, newCovEndUTC))
        saveManifest(config, account, newCovStartUTC, newCovEndUTC, earliestDataUTC)

    logger.info('History collection finished; csv={}'.format(csvPath))
