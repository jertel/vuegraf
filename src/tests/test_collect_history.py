# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Integration tests for collectHistoryUsage exercising the real CSV passthrough
# cache (vuegraf.cache) end-to-end: bootstrap, restore, uncovered-gap fetch,
# coverage-manifest persistence, and abort handling. Only the Emporia API
# (extractDataPoints / calculateHistoryTimeRange) and the InfluxDB writer are
# mocked; the cache and CSV I/O run for real against a temporary directory.

import csv
import datetime
import os
import tempfile
from argparse import Namespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from vuegraf import cache, collect
from vuegraf.collect import Point


UTC = datetime.timezone.utc


def _t(y, m, d, h=0):
    return datetime.datetime(y, m, d, h, tzinfo=UTC)


class TestCollectHistoryUsageCache(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)

        self.account = {'name': 'Acct', 'deviceIdMap': {123: 'Dev'}, 'vue': MagicMock()}
        self.account['vue'].get_device_list_usage.return_value = {123: MagicMock()}
        self.config = {'args': Namespace(skiprestore=False), 'csvCacheEnabled': True,
                       'influxDb': {}, 'timezone': None,
                       'clampNegativeUsage': False, 'csvCacheDir': 'backfill'}
        self.pause = MagicMock()
        self.pause.wait.return_value = False
        self.points = []

        # A single 20-day window, then a completion sentinel (start >= stop) to end the loop.
        self.patcher_calc = patch('vuegraf.collect.calculateHistoryTimeRange')
        self.mock_calc = self.patcher_calc.start()
        # Mock the API unpacking; concrete side effects are set per-test.
        self.patcher_extract = patch('vuegraf.collect.extractDataPoints')
        self.mock_extract = self.patcher_extract.start()
        # Isolate InfluxDB writes (collect writes fetched batches; cache writes restored points).
        self.patcher_wip_collect = patch('vuegraf.collect.writeDataPoints')
        self.mock_wip_collect = self.patcher_wip_collect.start()
        self.patcher_wip_cache = patch('vuegraf.cache.writeDataPoints')
        self.mock_wip_cache = self.patcher_wip_cache.start()

    def tearDown(self):
        self.patcher_calc.stop()
        self.patcher_extract.stop()
        self.patcher_wip_collect.stop()
        self.patcher_wip_cache.stop()
        os.chdir(self._cwd)

    def _oneWindow(self, start, stop):
        """calculateHistoryTimeRange side effect: emit [start, stop] once, then stop."""
        self.mock_calc.side_effect = [(start, stop), (stop, stop)]

    def _appendPoint(self, ts):
        """extractDataPoints side effect that appends one History point at ts."""
        def _se(config, account, device, rangeEnd, collectDetails, usageDataPoints,
                detailedStart, pointType, histStart, histEnd):
            usageDataPoints.append(Point('Acct', 'Dev', 'Ch', 42.0, ts, 'Hour'))
        self.mock_extract.side_effect = _se

    def test_fresh_run_fetches_writes_csv_and_saves_manifest(self):
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        self._oneWindow(start, stop)
        self._appendPoint(_t(2023, 1, 5))

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        # API path was used (no manifest yet) and a batch was written to InfluxDB.
        self.mock_extract.assert_called()
        self.mock_wip_collect.assert_called()
        # CSV cache now holds the fetched point.
        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], cache.CSV_HEADER)
        self.assertEqual(rows[1][1:3], ['Dev', 'Ch'])
        # Coverage manifest persisted, with earliestData at the fetched point.
        manifest = cache.loadManifest(self.config, self.account)
        self.assertEqual(manifest['coveredStartUTC'], start)
        self.assertEqual(manifest['earliestDataUTC'], _t(2023, 1, 5))

    def test_rerun_fully_covered_restores_and_skips_api(self):
        # Seed a manifest + CSV that fully cover the requested range.
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        cache.saveManifest(self.config, self.account, start, stop, _t(2023, 1, 5))
        path = cache.getCacheCsvPath(self.config, self.account)
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(cache.CSV_HEADER)
            w.writerow(['Acct', 'Dev', 'Ch', 42.0, _t(2023, 1, 5).isoformat(), 'Hour'])

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        # Restored from CSV, no API calls, no batch fetch.
        self.mock_wip_cache.assert_called()  # restore wrote to Influx
        self.mock_extract.assert_not_called()
        self.mock_wip_collect.assert_not_called()

    def test_skiprestore_skips_restore_when_fully_covered(self):
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        cache.saveManifest(self.config, self.account, start, stop, _t(2023, 1, 5))
        self.config['args'] = Namespace(skiprestore=True)

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        self.mock_wip_cache.assert_not_called()  # restore skipped
        self.mock_extract.assert_not_called()    # still fully covered -> no API

    def test_bootstrap_from_existing_csv_without_manifest(self):
        # CSV exists but no manifest; coverage is bootstrapped from the CSV contents.
        path = cache.getCacheCsvPath(self.config, self.account)
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(cache.CSV_HEADER)
            w.writerow(['Acct', 'Dev', 'Ch', 42.0, _t(2023, 1, 5).isoformat(), 'Hour'])
            w.writerow(['Acct', 'Dev', 'Ch', 42.0, _t(2023, 1, 10).isoformat(), 'Hour'])

        # Request the full bootstrapped span -> restore from CSV, no API fetch.
        collect.collectHistoryUsage(self.config, self.account, _t(2023, 1, 5), _t(2023, 1, 10), self.points, self.pause)

        self.mock_wip_cache.assert_called()
        self.mock_extract.assert_not_called()
        # A manifest is now persisted from the bootstrap.
        self.assertIsNotNone(cache.loadManifest(self.config, self.account))

    def test_abort_midway_only_advances_coverage_to_completed_batch(self):
        start, stop = _t(2023, 1, 1), _t(2023, 2, 10)
        b1s, b1e = _t(2023, 1, 1), _t(2023, 1, 21)
        b2s, b2e = _t(2023, 1, 21), _t(2023, 2, 10)
        # Two windows; pause fires after the first batch completes -> abort before the second.
        self.mock_calc.side_effect = [(b1s, b1e), (b2s, b2e)]
        self.pause.wait.side_effect = [True]
        self._appendPoint(_t(2023, 1, 5))

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        # Coverage advanced only to the first completed batch's stop, not the full request.
        manifest = cache.loadManifest(self.config, self.account)
        self.assertEqual(manifest['coveredStartUTC'], start)
        self.assertEqual(manifest['coveredEndUTC'], b1e)

    def test_abort_in_the_older_gap_does_not_claim_the_hole_it_left(self):
        """A partial head gap must not be merged across the unfetched middle.

        Coverage [Jan 10, Jan 20] with a request for [Jan 1, Jan 30] leaves an older gap
        [Jan 1, Jan 10]; aborting it at Jan 5 must not record [Jan 1, Jan 20] as probed,
        or [Jan 5, Jan 10] would never be fetched again.
        """
        cache.saveManifest(self.config, self.account, _t(2023, 1, 10), _t(2023, 1, 20), _t(2023, 1, 11))
        self.config['args'] = Namespace(skiprestore=True)
        self.mock_calc.side_effect = [(_t(2023, 1, 1), _t(2023, 1, 5))]
        self.pause.wait.side_effect = [True]
        self._appendPoint(_t(2023, 1, 2))

        collect.collectHistoryUsage(self.config, self.account, _t(2023, 1, 1), _t(2023, 1, 30), self.points, self.pause)

        manifest = cache.loadManifest(self.config, self.account)
        self.assertEqual(manifest['coveredStartUTC'], _t(2023, 1, 10))
        self.assertEqual(manifest['coveredEndUTC'], _t(2023, 1, 20))

    def test_completed_older_gap_extends_coverage_backwards(self):
        """The same shape, but the head gap runs to completion: coverage joins up."""
        cache.saveManifest(self.config, self.account, _t(2023, 1, 10), _t(2023, 1, 20), _t(2023, 1, 11))
        self.config['args'] = Namespace(skiprestore=True)
        # Head gap [Jan 1, Jan 10] completes, then the tail gap [Jan 20, Jan 30] completes.
        self.mock_calc.side_effect = [(_t(2023, 1, 1), _t(2023, 1, 10)), (_t(2023, 1, 10), _t(2023, 1, 10)),
                                      (_t(2023, 1, 20), _t(2023, 1, 30)), (_t(2023, 1, 30), _t(2023, 1, 30))]
        self._appendPoint(_t(2023, 1, 2))

        collect.collectHistoryUsage(self.config, self.account, _t(2023, 1, 1), _t(2023, 1, 30), self.points, self.pause)

        manifest = cache.loadManifest(self.config, self.account)
        self.assertEqual(manifest['coveredStartUTC'], _t(2023, 1, 1))
        self.assertEqual(manifest['coveredEndUTC'], _t(2023, 1, 30))

    def test_multiple_batches_append_to_same_csv(self):
        start, stop = _t(2023, 1, 1), _t(2023, 2, 10)
        b1s, b1e = _t(2023, 1, 1), _t(2023, 1, 21)
        b2s, b2e = _t(2023, 1, 21), _t(2023, 2, 10)
        self.mock_calc.side_effect = [(b1s, b1e), (b2s, b2e), (stop, stop)]
        # Distinct timestamps per batch, so this measures accumulation rather than
        # the de-duplication exercised by test_backfill_overlapping_a_rollup_compacts.
        timestamps = iter([_t(2023, 1, 5), _t(2023, 1, 25)])

        def _se(config, account, device, rangeEnd, collectDetails, usageDataPoints,
                detailedStart, pointType, histStart, histEnd):
            usageDataPoints.append(Point('Acct', 'Dev', 'Ch', 42.0, next(timestamps), 'Hour'))
        self.mock_extract.side_effect = _se

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        # Both batches landed in the one account CSV: header once, two data rows.
        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], cache.CSV_HEADER)
        self.assertEqual(len(rows), 3)

    def test_backfill_does_not_re_append_an_already_mirrored_reading(self):
        # The daemon mirrored an hour that a later backfill re-fetches. The reading is
        # still written to InfluxDB, but must not be appended to the shared file twice.
        start, ts, stop = _t(2023, 1, 1), _t(2023, 1, 5), _t(2023, 1, 21)
        cache.saveManifest(self.config, self.account, start, ts, start)
        cache.writeRollupPoints(self.config, self.account, [Point('Acct', 'Dev', 'Ch', 41.0, ts, 'Hour')])

        # Coverage ends at ts, so the single uncovered gap is the tail [ts, stop],
        # which re-fetches the already-mirrored hour.
        self._oneWindow(ts, stop)
        self._appendPoint(ts)

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], cache.CSV_HEADER)
        self.assertEqual(len(rows), 2)        # header + the single mirrored row
        self.assertEqual(rows[1][3], '41.0')  # the row the daemon already wrote
        self.mock_wip_collect.assert_called()  # still written to InfluxDB

    def test_backfill_does_not_re_append_earlier_hours_of_the_day_it_refetches(self):
        # Coverage ends mid-day, so the uncovered gap is only the last few minutes --
        # but calculateHistoryTimeRange floors the batch to local midnight, and the API
        # returns the whole day. The earlier hours of that day were already mirrored by
        # the daemon, and must not be appended a second time just because they sit
        # before the coverage end. Regression: every --historydays re-run grew the file.
        start = _t(2023, 1, 1)
        mirrored = _t(2023, 1, 20, 9)       # rollup wrote this hour earlier today
        covEnd = datetime.datetime(2023, 1, 20, 13, 40, tzinfo=UTC)
        stop = datetime.datetime(2023, 1, 20, 13, 47, tzinfo=UTC)
        cache.saveManifest(self.config, self.account, start, covEnd, start)
        cache.writeRollupPoints(self.config, self.account, [Point('Acct', 'Dev', 'Ch', 41.0, mirrored, 'Hour')])

        # The gap is [covEnd, stop], but the batch the API is actually asked for starts
        # at midnight and so re-delivers the 09:00 reading.
        self._oneWindow(_t(2023, 1, 20), stop)
        self._appendPoint(mirrored)

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 2)        # header + the single mirrored row
        self.assertEqual(rows[1][3], '41.0')  # the daemon's row, not a re-appended copy
        self.mock_wip_collect.assert_called()  # still written to InfluxDB

    def test_backfill_does_not_re_append_a_reading_older_than_the_batch_start(self):
        # The Day tier timestamps itself from Emporia's own bucket start rather than from
        # the start we asked for, so a batch can return a point OLDER than its start --
        # typically the previous day's summary. Anchoring the de-duplication window on the
        # batch start would leave that point unguarded and re-append it on every run.
        start = _t(2023, 1, 1)
        covEnd = datetime.datetime(2023, 1, 20, 13, 40, tzinfo=UTC)
        stop = datetime.datetime(2023, 1, 20, 13, 47, tzinfo=UTC)
        # Yesterday's daily summary, already mirrored by the daemon at the day rollover.
        priorDay = datetime.datetime(2023, 1, 19, 23, 59, 59, tzinfo=UTC)
        cache.saveManifest(self.config, self.account, start, covEnd, start)
        cache.writeRollupPoints(self.config, self.account, [Point('Acct', 'Dev', 'Ch', 41.0, priorDay, 'Day')])

        # The batch starts at midnight on the 20th, yet hands back a point stamped before it.
        self._oneWindow(_t(2023, 1, 20), stop)

        def _se(config, account, device, rangeEnd, collectDetails, usageDataPoints,
                detailedStart, pointType, histStart, histEnd):
            usageDataPoints.append(Point('Acct', 'Dev', 'Ch', 41.0, priorDay, 'Day'))
        self.mock_extract.side_effect = _se

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 2)        # header + the single mirrored row
        self.assertEqual(rows[1][4], priorDay.isoformat())
        self.mock_wip_collect.assert_called()  # still written to InfluxDB

    def test_head_gap_does_not_load_the_whole_file_into_the_key_set(self):
        # A gap reaching back BEFORE the current coverage anchors on its own old start.
        # Pairing that with an open-ended window would pull every later row in the file
        # into memory to guard a gap whose rows are almost all absent from it. The
        # window must stop near the gap's end.
        covStart, covEnd = _t(2023, 6, 1), _t(2023, 7, 1)
        cache.saveManifest(self.config, self.account, covStart, covEnd, covStart)
        # A row deep inside coverage, far past the head gap: must NOT be pulled in.
        farFuture = _t(2023, 6, 20)
        cache.writeRollupPoints(self.config, self.account, [Point('Acct', 'Dev', 'Ch', 41.0, farFuture, 'Hour')])

        # Request reaches back to January, so the head gap is [Jan 1, Jun 1].
        reqStart, gapEnd = _t(2023, 1, 1), covStart
        self.mock_calc.side_effect = [(reqStart, gapEnd), (gapEnd, gapEnd),
                                      (covEnd, covEnd)]  # tail gap immediately completes
        self._appendPoint(reqStart)

        with patch('vuegraf.collect.loadMirroredKeys', wraps=cache.loadMirroredKeys) as spy:
            collect.collectHistoryUsage(self.config, self.account, reqStart, covEnd, self.points, self.pause)

        spy.assert_called()
        sinceUTC, untilUTC = spy.call_args[0][2], spy.call_args[0][3]
        self.assertEqual(sinceUTC, reqStart)              # anchored on the batch's own data
        self.assertIsNotNone(untilUTC)                    # and bounded above
        self.assertLess(untilUTC, farFuture)              # so the far row is outside the window

        # The window really does exclude it, rather than merely being narrower on paper.
        keys = cache.loadMirroredKeys(self.config, self.account, sinceUTC, untilUTC)
        self.assertNotIn(('Dev', 'Ch', farFuture.isoformat(), 'Hour'), keys)

    def test_backfill_appends_readings_the_daemon_missed(self):
        # Same overlap window, but the daemon was down for this hour, so nothing was
        # mirrored. The backfill must still write it -- this is the case a coarse
        # skip-by-time-range would silently lose.
        start, mirrored, missed, stop = _t(2023, 1, 1), _t(2023, 1, 5), _t(2023, 1, 6), _t(2023, 1, 21)
        cache.saveManifest(self.config, self.account, start, mirrored, start)
        cache.writeRollupPoints(self.config, self.account, [Point('Acct', 'Dev', 'Ch', 41.0, mirrored, 'Hour')])

        self._oneWindow(mirrored, stop)
        self._appendPoint(missed)

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 3)  # header + mirrored hour + recovered hour
        self.assertEqual(rows[2][4], missed.isoformat())

    def test_empty_batch_writes_no_data_and_records_no_earliest(self):
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        self._oneWindow(start, stop)
        # extractDataPoints appends nothing (device offline / no history for the window).
        self.mock_extract.side_effect = lambda *a, **k: None

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        # The window was still probed and recorded (so it is not re-probed later), with no data.
        manifest = cache.loadManifest(self.config, self.account)
        self.assertEqual(manifest['coveredStartUTC'], start)
        self.assertIsNone(manifest['earliestDataUTC'])

    def test_manifest_present_but_request_disjoint_skips_restore_and_fetches_tail(self):
        # Manifest covers Jan; request is entirely in Mar -> no overlap to restore, tail is fetched.
        cache.saveManifest(self.config, self.account, _t(2023, 1, 1), _t(2023, 1, 31), _t(2023, 1, 5))
        r1, r2 = _t(2023, 3, 1), _t(2023, 3, 21)
        self._oneWindow(r1, r2)
        self._appendPoint(_t(2023, 3, 5))

        collect.collectHistoryUsage(self.config, self.account, r1, r2, self.points, self.pause)

        self.mock_wip_cache.assert_not_called()  # nothing overlapping to restore
        self.mock_extract.assert_called()        # tail fetched from API

    def test_gap_with_no_completed_batch_saves_no_new_coverage(self):
        # No manifest; the range's very first window is already at/after the stop, so no batch runs.
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        self.mock_calc.side_effect = [(stop, stop)]

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        self.mock_extract.assert_not_called()
        # Nothing completed and no prior coverage -> no manifest written.
        self.assertIsNone(cache.loadManifest(self.config, self.account))

    def test_cache_disabled_fetches_without_touching_the_cache(self):
        # Default configuration: the whole range is fetched from the API and written to
        # InfluxDB only. Nothing is read from or written to the cache directory.
        self.config['csvCacheEnabled'] = False
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        self._oneWindow(start, stop)
        self._appendPoint(_t(2023, 1, 5))

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        self.mock_extract.assert_called()
        self.mock_wip_collect.assert_called()
        self.assertFalse(os.path.exists(cache.getCacheCsvPath(self.config, self.account)))
        self.assertIsNone(cache.loadManifest(self.config, self.account))

    def test_cache_disabled_ignores_a_pre_existing_manifest(self):
        # A manifest left over from an earlier cached run must not suppress the API fetch
        # once caching is turned off.
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        cache.saveManifest(self.config, self.account, start, stop, _t(2023, 1, 5))
        self.config['csvCacheEnabled'] = False
        self._oneWindow(start, stop)
        self._appendPoint(_t(2023, 1, 5))

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        self.mock_wip_cache.assert_not_called()  # no restore
        self.mock_extract.assert_called()        # full range fetched from the API


class TestCollectHistoryUsageHierarchy(TestCase):
    """The history path nets each batch before persisting it, so the CSV mirror and
    InfluxDB both receive the Net Balance series without any extra plumbing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)

        self.account = {'name': 'Acct', 'deviceIdMap': {123: 'Dev'}, 'vue': MagicMock()}
        self.account['vue'].get_device_list_usage.return_value = {123: MagicMock()}
        self.config = {
            'args': Namespace(skiprestore=False),
            'timezone': None,
            'csvCacheEnabled': True,
            'clampNegativeUsage': False,
            'csvCacheDir': 'backfill',
            'influxDb': {},
            'hierarchyBalanceEpsilonWatts': 5.0,
            'hierarchyNegativeBalanceAbort': False,
            # A panel whose single circuit is separately metered by a plug.
            'hierarchy': {'Acct': {
                'nodes': {
                    'Panel': {'parent': None, 'children': ['Circuit'], 'kind': 'device',
                              'deviceName': 'Panel', 'explicit': False},
                    'Circuit': {'parent': 'Panel', 'children': [], 'kind': 'circuit',
                                'deviceName': 'Panel', 'explicit': False},
                },
                'roots': ['Panel'], 'virtualRoot': None, 'warned': set(), 'negativeWarnExpiry': {},
            }},
        }
        self.pause = MagicMock()
        self.pause.wait.return_value = False
        self.points = []

        self.patcher_calc = patch('vuegraf.collect.calculateHistoryTimeRange')
        self.mock_calc = self.patcher_calc.start()
        self.patcher_extract = patch('vuegraf.collect.extractDataPoints')
        self.mock_extract = self.patcher_extract.start()
        self.patcher_wip_collect = patch('vuegraf.collect.writeDataPoints')
        self.mock_wip_collect = self.patcher_wip_collect.start()
        self.patcher_wip_cache = patch('vuegraf.cache.writeDataPoints')
        self.mock_wip_cache = self.patcher_wip_cache.start()

    def tearDown(self):
        self.patcher_calc.stop()
        self.patcher_extract.stop()
        self.patcher_wip_collect.stop()
        self.patcher_wip_cache.stop()
        os.chdir(self._cwd)

    def test_batch_balances_reach_both_influx_and_the_csv(self):
        start, stop = _t(2023, 1, 1), _t(2023, 1, 21)
        self.mock_calc.side_effect = [(start, stop), (stop, stop)]
        ts = _t(2023, 1, 5, 3)

        def _se(config, account, device, rangeEnd, collectDetails, usageDataPoints,
                detailedStart, pointType, histStart, histEnd):
            usageDataPoints.append(Point('Acct', 'Panel', 'Panel', 1000.0, ts, 'Hour'))
            usageDataPoints.append(Point('Acct', 'Panel', 'Circuit', 400.0, ts, 'Hour'))
        self.mock_extract.side_effect = _se

        # Snapshot each batch: the list is cleared after the write to bound memory.
        batches = []
        self.mock_wip_collect.side_effect = lambda cfg, pts: batches.append(list(pts))

        collect.collectHistoryUsage(self.config, self.account, start, stop, self.points, self.pause)

        # The batch handed to InfluxDB carries the balance alongside the raw readings.
        self.assertEqual(len(batches), 1)
        written = batches[0]
        balances = [pt for pt in written if pt.chanName == 'Panel Net Balance']
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0].usageWatts, 600.0)  # 1000 - 400
        self.assertEqual(len(written), 3)

        # The same list is what was appended to the CSV mirror, so it is there too.
        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            rows = list(csv.reader(f))[1:]
        self.assertIn(['Acct', 'Panel', 'Panel Net Balance', '600.0', ts.isoformat(), 'Hour'], rows)
