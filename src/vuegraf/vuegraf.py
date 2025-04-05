#!/usr/bin/env python3
"""
Main Vuegraf execution script.

Handles configuration loading, data collection scheduling, InfluxDB writing,
and graceful shutdown.
"""
# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

__author__ = 'https://github.com/jertel'
__license__ = 'MIT'
__contributors__ = 'https://github.com/jertel/vuegraf/graphs/contributors'
__version__ = '1.9.0'
__versiondate__ = '2025/04/05'
__maintainer__ = 'https://github.com/jertel'
__status__ = 'Production'

import datetime
import logging
import signal
import sys
import threading
import traceback
from pyemvue.enums import Scale

# Local imports
from vuegraf.collect import collectHistoryUsage, collectUsage
from vuegraf.config import getConfigValue, initConfig
from vuegraf.device import initDeviceAccount
from vuegraf.influx import initInfluxConnection, writeInfluxPoints
from vuegraf.time import getCurrentHourUTC, getCurrentDayLocal, getTimeNow


logger = logging.getLogger('vuegraf')
pauseEvent = threading.Event()


def run():
    global running

    config = initConfig()
    logger.info('Starting Vuegraf version {}'.format(__version__))

    initInfluxConnection(config)

    detailedStartTimeUTC = getTimeNow(datetime.UTC)

    maxHistoryDays = getConfigValue(config, 'maxHistoryDays')
    historyDays = min(config['args'].historydays, maxHistoryDays)
    historyEnabled = historyDays > 0

    intervalSecs = getConfigValue(config, 'updateIntervalSecs')

    detailedIntervalSecs = getConfigValue(config, 'detailedIntervalSecs')
    detailedDataEnabled = getConfigValue(config, 'detailedDataEnabled')
    detailedDaysEnabled = detailedDataEnabled and getConfigValue(config, 'detailedDataDaysEnabled')
    detailedHoursEnabled = detailedDataEnabled and getConfigValue(config, 'detailedDataHoursEnabled')

    # Initialize vars to track when an hour or day changes which will trigger hourly/daily averages
    prevHourUTC = getCurrentHourUTC()
    prevDayLocal = getCurrentDayLocal(config)

    running = True
    while running:
        usageDataPoints = []

        # Set updated vars to compare with previous run
        curHourUTC = getCurrentHourUTC()
        curDayLocal = getCurrentDayLocal(config)

        nowUTC = getTimeNow(datetime.UTC)
        nowLagUTC = nowUTC - datetime.timedelta(seconds=getConfigValue(config, 'lagSecs'))

        # Determine whether detailed "second" data should be collected on this run
        secondsSinceLastDetailCollection = (nowLagUTC - detailedStartTimeUTC).total_seconds()
        collectDetails = detailedDataEnabled and detailedIntervalSecs > 0 and secondsSinceLastDetailCollection >= detailedIntervalSecs

        logger.debug('Starting next event collection; collectDetails={}; secondsSinceLastDetailCollection={}; detailedIntervalSecs={}'
                     .format(collectDetails, secondsSinceLastDetailCollection, detailedIntervalSecs))

        for account in config['accounts']:
            initDeviceAccount(config, account)

            try:
                if historyEnabled:
                    logger.info('Loading historical data; historyDays={}'.format(historyDays))

                    # Start at current time (minus a small lag) and go back in time by `historyDays` days
                    historyStartTimeUTC = nowLagUTC - datetime.timedelta(historyDays)

                    collectHistoryUsage(config, account, historyStartTimeUTC, nowLagUTC, usageDataPoints, pauseEvent)
                else:
                    # Collect current usage data for the last interval for each device in the current account
                    collectUsage(config, account, None, nowLagUTC, collectDetails, usageDataPoints,
                                 detailedStartTimeUTC, Scale.MINUTE.value)

                    # Collect hourly averages if the hour just changed. Use UTC time for this to avoid DST
                    # issues.
                    if detailedHoursEnabled and curHourUTC != prevHourUTC:
                        collectUsage(config, account, prevHourUTC, prevHourUTC, False, usageDataPoints, None, Scale.HOUR.value)
                        prevHourUTC = curHourUTC

                    # Collect daily averages if the day just changed. Note that this is local time
                    # If used UTC was used it would attempt to collect the day's average before the local
                    # day was complete (for UTC-X timezones)
                    if detailedDaysEnabled and curDayLocal != prevDayLocal:
                        prevDayUTC = prevDayLocal.astimezone(datetime.UTC)
                        collectUsage(config, account, prevDayUTC, prevDayUTC, False, usageDataPoints, None, Scale.DAY.value)
                        prevDayLocal = curDayLocal

            except Exception:
                logger.error('Failed to record new usage data: {}'.format(sys.exc_info()))
                traceback.print_exc()

            if not running:
                break

        # Save accumulated data points into InfluxDB
        writeInfluxPoints(config, usageDataPoints)

        if collectDetails:
            detailedStartTimeUTC = nowLagUTC + datetime.timedelta(seconds=1)

        # Only run history collection once per each account
        historyEnabled = False

        # Sleep for the specified interval before starting the next collection
        pauseEvent.wait(intervalSecs)

    logger.info('Finished')


def handleExitSignal(signum, frame):
    global running
    logger.error('Caught exit signal')
    running = False
    pauseEvent.set()


def main(args=None):
    try:
        signal.signal(signal.SIGINT, handleExitSignal)
        signal.signal(signal.SIGHUP, handleExitSignal)
        run()
    except SystemExit as e:
        # If sys.exit was 2, then normal syntax exit from help or bad command line, no error message
        if e.code == 0 or e.code == 2:
            quit(0)
        else:
            # Catch other SystemExit codes (besides 0 and 2)
            logger.error('Fatal system exit: {}'.format(sys.exc_info()))
            traceback.print_exc()
    except Exception:
        # Catch any other unexpected exceptions
        logger.error('Fatal error: {}'.format(sys.exc_info()))
        traceback.print_exc()


if __name__ == '__main__':
    main(sys.argv[1:])  # pragma: no cover
