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
__version__ = '1.10.1'
__versiondate__ = '2025/12/24'
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
from vuegraf.destination import closeConnection, initConnection, writeDataPoints
from vuegraf.time import getCurrentHourUTC, getCurrentDayLocal, getTimeNow


logger = logging.getLogger('vuegraf')
pauseEvent = threading.Event()


def run():
    global running

    config = initConfig()
    logger.info('Starting Vuegraf version {}'.format(__version__))

    initConnection(config)

    detailedStartTimeUTC = getTimeNow(datetime.UTC)

    maxHistoryDays = getConfigValue(config, 'maxHistoryDays')
    historyDays = min(config['args'].historydays, maxHistoryDays)
    historyEnabled = historyDays > 0

    intervalSecs = getConfigValue(config, 'updateIntervalSecs')

    detailedIntervalSecs = getConfigValue(config, 'detailedIntervalSecs')
    detailedDataEnabled = getConfigValue(config, 'detailedDataEnabled')
    daysEnabled = getConfigValue(config, 'detailedDataDaysEnabled')
    hoursEnabled = getConfigValue(config, 'detailedDataHoursEnabled')

    # Initialize vars to track when an hour or day changes which will trigger hourly/daily averages.
    # Each account keeps its own, so one account collecting a rollup -- or failing to -- does not
    # decide whether the next one does.
    startHourUTC = getCurrentHourUTC()
    startDayLocal = getCurrentDayLocal(config)
    prevHourUTC = {index: startHourUTC for index in range(len(config['accounts']))}
    prevDayLocal = {index: startDayLocal for index in range(len(config['accounts']))}

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

        for index, account in enumerate(config['accounts']):
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

                    # Hourly and daily averages are detail data, except for an account with a device
                    # hierarchy: its Net Balances are derived from them, so they are collected for it
                    # whenever their own resolution is enabled.
                    rollupsEnabled = detailedDataEnabled or account.get('hierarchy') is not None

                    # Collect hourly averages if the hour just changed. Use UTC time for this to avoid DST
                    # issues.
                    if rollupsEnabled and hoursEnabled and curHourUTC != prevHourUTC[index]:
                        collectUsage(config, account, prevHourUTC[index], prevHourUTC[index], False, usageDataPoints, None,
                                     Scale.HOUR.value)
                        prevHourUTC[index] = curHourUTC

                    # Collect daily averages if the day just changed. Note that this is local time
                    # If used UTC was used it would attempt to collect the day's average before the local
                    # day was complete (for UTC-X timezones)
                    if rollupsEnabled and daysEnabled and curDayLocal != prevDayLocal[index]:
                        prevDayUTC = prevDayLocal[index].astimezone(datetime.UTC)
                        collectUsage(config, account, prevDayUTC, prevDayUTC, False, usageDataPoints, None, Scale.DAY.value)
                        prevDayLocal[index] = curDayLocal

            except Exception:
                logger.error('Failed to record new usage data: {}'.format(sys.exc_info()))
                traceback.print_exc()

            if not running:
                break

        # Save accumulated data points into the configured destinations
        writeDataPoints(config, usageDataPoints)

        if collectDetails:
            detailedStartTimeUTC = nowLagUTC + datetime.timedelta(seconds=1)

        # Only run history collection once per each account
        historyEnabled = False

        # Sleep for the specified interval before starting the next collection
        pauseEvent.wait(intervalSecs)

    closeConnection(config)
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
