# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to timezones and time calculations.

import datetime
import pytz

# Local imports
from vuegraf.config import getConfigValue


def getTimezone(config):
    timezoneName = getConfigValue(config, 'timezone')
    timezone = None
    if timezoneName is not None:
        timezone = pytz.timezone(timezoneName)
    return timezone


def getCurrentHourUTC():
    return getTimeNow(datetime.UTC).replace(minute=0, second=0, microsecond=0)


def getCurrentDayLocal(config):
    return getTimeNow(getTimezone(config)).replace(hour=23, minute=59, second=59, microsecond=0)


def getTimeNow(timezone):
    return datetime.datetime.now(timezone).replace(microsecond=0)


def convertToLocalDayInUTC(config, timestamp):
    timestamp = timestamp.astimezone(getTimezone(config))
    timestamp = timestamp.replace(hour=23, minute=59, second=59, microsecond=0)
    timestamp = timestamp.astimezone(pytz.UTC)
    return timestamp


def getLocalDayStartUTC(config, timestamp):
    """Midnight of the timestamp's local day, expressed in UTC.

    The lower edge of the day whose summary is still accumulating, so it is the
    newest instant everything before which has settled at every mirrored tier.
    """
    timestamp = timestamp.astimezone(getTimezone(config))
    timestamp = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return timestamp.astimezone(pytz.UTC)


def calculateHistoryTimeRange(config, nowLagUTC, startTimeUTC, historyIncrements):
    historySizeDays = 20  # Default to 20 days of history per increment
    timezone = getTimezone(config)
    startTimeUTC = startTimeUTC + datetime.timedelta(days=historyIncrements * historySizeDays)
    startTimeLocal = startTimeUTC.astimezone(timezone)
    startTimeLocal = startTimeLocal.replace(hour=0, minute=0, second=0, microsecond=0)

    startTimeUTC = startTimeLocal.astimezone(datetime.UTC)

    stopTimeUTC = startTimeUTC + datetime.timedelta(days=historySizeDays - 1)
    stopTimeUTC = stopTimeUTC.astimezone(timezone).replace(hour=23, minute=59, second=59, microsecond=0).astimezone(datetime.UTC)
    stopTimeUTC = min(stopTimeUTC, nowLagUTC)

    return startTimeUTC, stopTimeUTC


def calculateResumeTimeRange(config, timeStr, pointType, tagValue_second, tagValue_minute,
                             startTime, stopTime, fillInMissingData):
    """Calculates the time range to request from the Emporia API when resuming collection.

    Given the timestamp of the most recent record already stored (timeStr, empty when no
    record was found), returns the (startTime, stopTime, fillInMissingData) window to
    fetch. The adjustments applied here are constraints of the Emporia API - how far back
    history is available, and how much can be retrieved in a single call - so they are
    independent of which destination the last record came from.

    The resolution tag values are passed in rather than looked up, since each destination
    owns its own tag naming.
    """
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
