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
