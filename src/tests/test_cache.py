# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import csv
import datetime
import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

from vuegraf import cache
from vuegraf.collect import Point


UTC = datetime.timezone.utc


def _t(y, m, d, h=0):
    return datetime.datetime(y, m, d, h, tzinfo=UTC)


class TestSubtractCoverage(TestCase):
    def test_no_existing_coverage_returns_whole_range(self):
        self.assertEqual(
            cache.subtractCoverage(_t(2023, 1, 1), _t(2026, 7, 1), None, None),
            [(_t(2023, 1, 1), _t(2026, 7, 1))],
        )

    def test_fully_covered_returns_empty(self):
        self.assertEqual(
            cache.subtractCoverage(_t(2024, 1, 1), _t(2024, 6, 1), _t(2023, 1, 1), _t(2026, 7, 1)),
            [],
        )

    def test_tail_only_when_time_advances(self):
        self.assertEqual(
            cache.subtractCoverage(_t(2023, 1, 1), _t(2026, 7, 15), _t(2023, 1, 1), _t(2026, 7, 1)),
            [(_t(2026, 7, 1), _t(2026, 7, 15))],
        )

    def test_head_and_tail_when_range_extends_both_directions(self):
        # Requested range now reaches further back AND further forward than cached coverage.
        self.assertEqual(
            cache.subtractCoverage(_t(2021, 1, 1), _t(2026, 7, 15), _t(2023, 1, 1), _t(2026, 7, 1)),
            [(_t(2021, 1, 1), _t(2023, 1, 1)), (_t(2026, 7, 1), _t(2026, 7, 15))],
        )


class TestMergeCoverage(TestCase):
    def test_disjoint_pieces_are_sorted_and_kept_apart(self):
        self.assertEqual(
            cache.mergeCoverage([(_t(2023, 3, 1), _t(2023, 4, 1)), (_t(2023, 1, 1), _t(2023, 2, 1))]),
            [(_t(2023, 1, 1), _t(2023, 2, 1)), (_t(2023, 3, 1), _t(2023, 4, 1))],
        )

    def test_abutting_and_overlapping_pieces_are_joined(self):
        self.assertEqual(
            cache.mergeCoverage([(_t(2023, 1, 1), _t(2023, 2, 1)), (_t(2023, 2, 1), _t(2023, 3, 1)),
                                 (_t(2023, 2, 15), _t(2023, 4, 1))]),
            [(_t(2023, 1, 1), _t(2023, 4, 1))],
        )

    def test_contained_piece_does_not_shrink_the_window(self):
        self.assertEqual(
            cache.mergeCoverage([(_t(2023, 1, 1), _t(2023, 4, 1)), (_t(2023, 2, 1), _t(2023, 3, 1))]),
            [(_t(2023, 1, 1), _t(2023, 4, 1))],
        )


class TestCacheFilesystem(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self.account = {'name': 'Test Acct'}
        self.config = {'influxDb': {}, 'timezone': None,
                       'csvCacheEnabled': True, 'csvCacheDir': 'backfill', 'args': None}

    def tearDown(self):
        os.chdir(self._cwd)

    def _writeCsv(self, rows):
        """Seed the account's single cache CSV."""
        path = cache.getCacheCsvPath(self.config, self.account)
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(cache.CSV_HEADER)
            for r in rows:
                w.writerow(r)
        return path

    def test_account_slug_spaces_replaced(self):
        path = cache.getCacheCsvPath(self.config, self.account)
        self.assertEqual(os.path.basename(path), 'Test_Acct.csv')

    def test_account_slug_cannot_escape_the_cache_dir(self):
        """A path separator or traversal segment in the account name stays inert."""
        cacheDir = cache.getCacheDir(self.config)
        for name in ['../../etc/passwd', 'sub/acct', '..']:
            for path in [cache.getCacheCsvPath(self.config, {'name': name}),
                         cache.getManifestPath(self.config, {'name': name})]:
                self.assertEqual(os.path.dirname(os.path.abspath(path)), cacheDir)

    def test_account_slug_falls_back_when_nothing_survives(self):
        self.assertEqual(os.path.basename(cache.getCacheCsvPath(self.config, {'name': ''})), 'account.csv')

    def test_cache_enabled_reflects_config(self):
        self.assertTrue(cache.isCacheEnabled(self.config))
        self.assertFalse(cache.isCacheEnabled({'csvCacheEnabled': False}))

    def test_cache_dir_is_created_and_absolute(self):
        cacheDir = cache.getCacheDir(self.config)
        self.assertTrue(os.path.isabs(cacheDir))
        self.assertTrue(os.path.isdir(cacheDir))

    def test_cache_dir_honors_configured_location(self):
        custom = os.path.join(self.tmp, 'custom-cache')
        cacheDir = cache.getCacheDir({'csvCacheDir': custom})
        self.assertEqual(cacheDir, custom)
        self.assertTrue(os.path.isdir(custom))

    def test_manifest_round_trip_preserves_utc_instants(self):
        cache.saveManifest(self.config, self.account, _t(2023, 1, 1), _t(2026, 7, 1), _t(2023, 1, 11))
        loaded = cache.loadManifest(self.config, self.account)
        self.assertEqual(loaded['coveredStartUTC'], _t(2023, 1, 1))
        self.assertEqual(loaded['coveredEndUTC'], _t(2026, 7, 1))
        self.assertEqual(loaded['earliestDataUTC'], _t(2023, 1, 11))

    def test_load_manifest_absent_returns_none(self):
        self.assertIsNone(cache.loadManifest(self.config, self.account))

    def test_bootstrap_from_csvs_finds_min_max_and_earliest(self):
        self._writeCsv([
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 1, 11, 5).isoformat(), 'Hour'],
            ['Test Acct', 'Dev', 'Ch', 200.0, _t(2023, 6, 2, 5).isoformat(), 'Day'],
        ])
        covStart, covEnd, earliest = cache.bootstrapManifestFromCsvs(self.config, self.account)
        self.assertEqual(covStart, _t(2023, 1, 11, 5))
        self.assertEqual(covEnd, _t(2023, 6, 2, 5))
        self.assertEqual(earliest, _t(2023, 1, 11, 5))

    def test_bootstrap_with_no_data_returns_none(self):
        self._writeCsv([])  # header only
        self.assertIsNone(cache.bootstrapManifestFromCsvs(self.config, self.account))

    def test_bootstrap_handles_non_monotonic_timestamps(self):
        # Rows out of order: the later row is neither a new min nor a new max.
        self._writeCsv([
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 6, 2, 5).isoformat(), 'Day'],
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 1, 11, 5).isoformat(), 'Hour'],
        ])
        covStart, covEnd, earliest = cache.bootstrapManifestFromCsvs(self.config, self.account)
        self.assertEqual(covStart, _t(2023, 1, 11, 5))
        self.assertEqual(covEnd, _t(2023, 6, 2, 5))
        self.assertEqual(earliest, _t(2023, 1, 11, 5))

    def test_bootstrap_with_no_csv_returns_none(self):
        self.assertIsNone(cache.bootstrapManifestFromCsvs(self.config, self.account))

    def test_bootstrap_sees_rollup_points_in_the_shared_file(self):
        # Rollups share the account file, so a lost manifest is bootstrapped from
        # them too. This is coarse by design: it cannot see gaps.
        cache.writeRollupPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 100.0, _t(2026, 8, 1, 5), 'Hour'),
        ])
        covStart, covEnd, earliest = cache.bootstrapManifestFromCsvs(self.config, self.account)
        self.assertEqual(covStart, _t(2026, 8, 1, 5))
        self.assertEqual(covEnd, _t(2026, 8, 1, 5))

    @patch('vuegraf.cache.writeDataPoints')
    def test_restore_filters_by_range_and_reconstructs_points(self, mock_write):
        self._writeCsv([
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 1, 11, 5).isoformat(), 'Hour'],
            ['Test Acct', 'Dev', 'Ch', 200.0, _t(2023, 1, 12, 5).isoformat(), 'Day'],
        ])
        restored = cache.restoreCacheToDatabase(
            self.config, self.account, _t(2023, 1, 11, 0), _t(2023, 1, 11, 23))
        self.assertEqual(restored, 1)
        written = mock_write.call_args[0][1]
        self.assertEqual(len(written), 1)
        self.assertEqual(
            written[0],
            Point('Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 1, 11, 5), 'Hour'),
        )

    @patch('vuegraf.cache.writeDataPoints')
    def test_restore_with_no_csv_returns_zero(self, mock_write):
        self.assertEqual(
            cache.restoreCacheToDatabase(self.config, self.account, _t(2023, 1, 1), _t(2023, 12, 31)), 0)
        mock_write.assert_not_called()

    @patch('vuegraf.cache.writeDataPoints')
    def test_restore_replays_history_and_rollup_rows_together(self, mock_write):
        # Both collection paths share one file, so a restore after a DB reset
        # replays backfilled history and mirrored rollups in a single pass.
        self._writeCsv([['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 1, 11, 5).isoformat(), 'Hour']])
        cache.writeRollupPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 250.0, _t(2023, 1, 11, 6), 'Hour'),
        ])
        restored = cache.restoreCacheToDatabase(
            self.config, self.account, _t(2023, 1, 11, 0), _t(2023, 1, 12, 0))
        self.assertEqual(restored, 2)

    def test_manifest_with_null_fields_round_trips_as_none(self):
        # earliestData unknown (None) must survive save (->null) and load (->None).
        cache.saveManifest(self.config, self.account, _t(2023, 1, 1), _t(2023, 6, 1), None)
        loaded = cache.loadManifest(self.config, self.account)
        self.assertIsNone(loaded['earliestDataUTC'])
        self.assertEqual(loaded['coveredStartUTC'], _t(2023, 1, 1))

    def test_load_corrupt_manifest_returns_none(self):
        with open(cache.getManifestPath(self.config, self.account), 'w') as f:
            f.write('{ this is not valid json')
        self.assertIsNone(cache.loadManifest(self.config, self.account))

    def test_bootstrap_skips_malformed_rows(self):
        self._writeCsv([
            ['Test Acct', 'Dev', 'Ch'],                                              # too few columns
            ['Test Acct', 'Dev', 'Ch', 100.0, 'not-a-timestamp', 'Hour'],            # unparseable timestamp
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 3, 1, 5).isoformat(), 'Hour'],  # valid
        ])
        covStart, covEnd, earliest = cache.bootstrapManifestFromCsvs(self.config, self.account)
        self.assertEqual(covStart, _t(2023, 3, 1, 5))
        self.assertEqual(covEnd, _t(2023, 3, 1, 5))
        self.assertEqual(earliest, _t(2023, 3, 1, 5))

    @patch('vuegraf.cache.writeDataPoints')
    def test_restore_skips_malformed_and_out_of_range_rows(self, mock_write):
        self._writeCsv([
            ['Test Acct', 'Dev', 'Ch', 100.0, 'not-a-timestamp', 'Hour'],            # bad timestamp
            ['Test Acct', 'Dev', 'Ch', 'not-a-number', _t(2023, 3, 1, 5).isoformat(), 'Hour'],  # bad watts
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2020, 1, 1, 5).isoformat(), 'Hour'],  # out of range (before)
            ['Test Acct', 'Dev', 'Ch'],                                              # too few columns
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 3, 1, 5).isoformat(), 'Hour'],  # valid, in range
        ])
        restored = cache.restoreCacheToDatabase(
            self.config, self.account, _t(2023, 1, 1), _t(2023, 12, 31))
        self.assertEqual(restored, 1)

    @patch('vuegraf.cache.RESTORE_CHUNK', 1)
    @patch('vuegraf.cache.writeDataPoints')
    def test_restore_flushes_mid_stream_when_chunk_reached(self, mock_write):
        self._writeCsv([
            ['Test Acct', 'Dev', 'Ch', 100.0, _t(2023, 3, 1, 5).isoformat(), 'Hour'],
            ['Test Acct', 'Dev', 'Ch', 200.0, _t(2023, 3, 2, 5).isoformat(), 'Hour'],
        ])
        restored = cache.restoreCacheToDatabase(
            self.config, self.account, _t(2023, 1, 1), _t(2023, 12, 31))
        self.assertEqual(restored, 2)
        # With a chunk size of 1, each in-range point flushes immediately (mid-stream, not just at end).
        self.assertEqual(mock_write.call_count, 2)


class TestRollupMirror(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self.account = {'name': 'Test Acct'}
        self.config = {'influxDb': {}, 'timezone': None,
                       'csvCacheEnabled': True, 'csvCacheDir': 'backfill'}

    def tearDown(self):
        os.chdir(self._cwd)

    def _rollupRows(self):
        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            return list(csv.reader(f))

    def test_disabled_cache_writes_nothing(self):
        config = {'csvCacheEnabled': False, 'csvCacheDir': 'backfill'}
        written = cache.writeRollupPoints(config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 100.0, _t(2026, 8, 1, 5), 'Hour'),
        ])
        self.assertEqual(written, 0)
        self.assertFalse(os.path.exists(cache.getCacheCsvPath(self.config, self.account)))

    def test_no_points_writes_nothing(self):
        self.assertEqual(cache.writeRollupPoints(self.config, self.account, []), 0)
        self.assertFalse(os.path.exists(cache.getCacheCsvPath(self.config, self.account)))

    def test_points_are_appended_with_a_single_header(self):
        cache.writeRollupPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 100.0, _t(2026, 8, 1, 5), 'Hour'),
        ])
        written = cache.writeRollupPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 200.0, _t(2026, 8, 2, 5), 'Day'),
        ])
        self.assertEqual(written, 1)

        rows = self._rollupRows()
        self.assertEqual(rows[0], cache.CSV_HEADER)
        self.assertEqual(len(rows), 3)  # header + two appended points
        self.assertEqual(rows[1][3], '100.0')
        self.assertEqual(rows[1][5], 'Hour')
        self.assertEqual(rows[2][4], _t(2026, 8, 2, 5).isoformat())
        self.assertEqual(rows[2][5], 'Day')


class TestMirroredKeys(TestCase):
    """Keys read back so a backfill does not re-append what the daemon mirrored."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self.account = {'name': 'Test Acct'}
        self.config = {'influxDb': {}, 'timezone': None,
                       'csvCacheEnabled': True, 'csvCacheDir': 'backfill'}

    def tearDown(self):
        os.chdir(self._cwd)

    def test_point_key_identifies_a_series_instant_and_resolution(self):
        ts = _t(2026, 8, 1, 5)
        base = Point('Test Acct', 'Dev', 'Ch', 100.0, ts, 'Hour')
        self.assertEqual(cache.pointKey(base), cache.pointKey(Point('Test Acct', 'Dev', 'Ch', 999.0, ts, 'Hour')))
        self.assertNotEqual(cache.pointKey(base), cache.pointKey(Point('Test Acct', 'Dev', 'Ch', 100.0, ts, 'Day')))
        self.assertNotEqual(cache.pointKey(base), cache.pointKey(Point('Test Acct', 'Dev', 'Other', 100.0, ts, 'Hour')))

    def test_no_coverage_yet_means_no_possible_overlap(self):
        cache.writeRollupPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 100.0, _t(2026, 8, 1, 5), 'Hour'),
        ])
        self.assertEqual(cache.loadMirroredKeys(self.config, self.account, None), set())

    def test_missing_file_returns_empty_set(self):
        self.assertEqual(cache.loadMirroredKeys(self.config, self.account, _t(2026, 8, 1)), set())

    def test_only_keys_at_or_after_the_coverage_end_are_loaded(self):
        # Older rows came from an earlier backfill, which the gap calculation already
        # excludes, so loading them would be wasted memory.
        cache.writeRollupPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 100.0, _t(2026, 7, 1, 5), 'Hour'),   # before coverage end
            Point('Test Acct', 'Dev', 'Ch', 200.0, _t(2026, 8, 1, 5), 'Hour'),   # at/after
            Point('Test Acct', 'Dev', 'Ch', 300.0, _t(2026, 8, 2, 5), 'Day'),    # at/after
        ])
        keys = cache.loadMirroredKeys(self.config, self.account, _t(2026, 8, 1))
        self.assertEqual(keys, {
            ('Dev', 'Ch', _t(2026, 8, 1, 5).isoformat(), 'Hour'),
            ('Dev', 'Ch', _t(2026, 8, 2, 5).isoformat(), 'Day'),
        })

    def test_malformed_rows_are_ignored(self):
        path = cache.getCacheCsvPath(self.config, self.account)
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(cache.CSV_HEADER)
            w.writerow(['Test Acct', 'Dev', 'Ch'])  # truncated
            w.writerow(['Test Acct', 'Dev', 'Ch', 100.0, _t(2026, 8, 1, 5).isoformat(), 'Hour'])
        keys = cache.loadMirroredKeys(self.config, self.account, _t(2026, 8, 1))
        self.assertEqual(keys, {('Dev', 'Ch', _t(2026, 8, 1, 5).isoformat(), 'Hour')})

    def test_keys_match_the_points_a_backfill_would_produce(self):
        # The round trip that matters: a mirrored point's key must equal the key of
        # the equivalent point re-fetched by a backfill.
        ts = _t(2026, 8, 1, 5)
        cache.writeRollupPoints(self.config, self.account, [Point('Test Acct', 'Dev', 'Ch', 100.0, ts, 'Hour')])
        keys = cache.loadMirroredKeys(self.config, self.account, _t(2026, 8, 1))
        self.assertIn(cache.pointKey(Point('Test Acct', 'Dev', 'Ch', 111.0, ts, 'Hour')), keys)


class TestSettledOnlyMirroring(TestCase):
    """Only closed periods reach the CSV.

    An open period's average is provisional; whoever collects it after it closes
    writes the final figure under the same key. InfluxDB absorbs that as an
    overwrite, but the append-only CSV would keep both rows.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self.account = {'name': 'Test Acct'}
        self.config = {'influxDb': {}, 'timezone': None,
                       'csvCacheEnabled': True, 'csvCacheDir': 'backfill'}

    def tearDown(self):
        os.chdir(self._cwd)

    def _rows(self):
        with open(cache.getCacheCsvPath(self.config, self.account), newline='') as f:
            return list(csv.reader(f))[1:]

    def test_open_hour_is_held_back_until_it_closes(self):
        now = datetime.datetime(2026, 8, 29, 16, 47, tzinfo=UTC)
        openHour = _t(2026, 8, 29, 16)      # closes at 17:00, after 'now'
        closedHour = _t(2026, 8, 29, 15)    # closed at 16:00
        written = cache.mirrorPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 1.0, openHour, 'Hour'),
            Point('Test Acct', 'Dev', 'Ch', 2.0, closedHour, 'Hour'),
        ], nowUTC=now)
        self.assertEqual(written, 1)
        self.assertEqual([r[4] for r in self._rows()], [closedHour.isoformat()])

    def test_open_day_is_held_back_until_it_closes(self):
        # Day points are stamped at the END of their local day, so a stamp in the
        # future means the day is still accumulating.
        now = datetime.datetime(2026, 8, 29, 16, 47, tzinfo=UTC)
        openDay = datetime.datetime(2026, 8, 30, 3, 59, tzinfo=UTC)     # local day 08-29, still open
        closedDay = datetime.datetime(2026, 8, 29, 3, 59, tzinfo=UTC)   # local day 08-28, closed
        written = cache.mirrorPoints(self.config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 1.0, openDay, 'Day'),
            Point('Test Acct', 'Dev', 'Ch', 2.0, closedDay, 'Day'),
        ], nowUTC=now)
        self.assertEqual(written, 1)
        self.assertEqual([r[4] for r in self._rows()], [closedDay.isoformat()])

    def test_the_provisional_then_settled_sequence_yields_one_row(self):
        # The exact production sequence: a backfill mid-day, then the daemon's
        # rollover write for the same day. Previously two rows; now one.
        openDay = datetime.datetime(2026, 8, 30, 3, 59, tzinfo=UTC)
        cache.mirrorPoints(self.config, self.account,
                           [Point('Test Acct', 'Dev', 'Ch', 111.0, openDay, 'Day')],
                           nowUTC=datetime.datetime(2026, 8, 29, 16, 47, tzinfo=UTC))      # backfill, day still open
        cache.mirrorPoints(self.config, self.account,
                           [Point('Test Acct', 'Dev', 'Ch', 222.0, openDay, 'Day')],
                           nowUTC=datetime.datetime(2026, 8, 30, 4, 1, tzinfo=UTC))        # daemon, after rollover
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], '222.0')  # the settled value

    def test_a_tier_other_than_hour_or_day_is_never_held_back(self):
        # Only Hour/Day are mirrored, so an unrecognised tier must pass through
        # rather than be silently dropped as "unsettled".
        written = cache.mirrorPoints(self.config, self.account,
                                     [Point('Test Acct', 'Dev', 'Ch', 1.0, _t(2026, 8, 1, 5), 'Minutes')],
                                     nowUTC=_t(2026, 8, 1))
        self.assertEqual(written, 1)


class TestSettledGateReadsTheConfiguredDestination(TestCase):
    """The settled-period gate takes its resolution tags from whichever database is configured.

    The gate compares a point's `detailed` tag against the Hour and Day tag values, and
    those are per-destination settings. Reading them from a fixed influxDb section would
    raise KeyError on a VictoriaMetrics-only install, and would silently mirror
    provisional values for anyone who renamed the tags there.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self.account = {'name': 'Test Acct'}

    def tearDown(self):
        os.chdir(self._cwd)

    def _config(self, **overrides):
        config = {'timezone': None, 'csvCacheEnabled': True, 'csvCacheDir': 'backfill'}
        config.update(overrides)
        return config

    def test_victoria_metrics_only_install_holds_back_an_open_hour(self):
        config = self._config(victoriaMetrics={'url': 'http://vm:8428'})
        now = datetime.datetime(2026, 8, 29, 16, 47, tzinfo=UTC)
        written = cache.mirrorPoints(config, self.account, [
            Point('Test Acct', 'Dev', 'Ch', 1.0, _t(2026, 8, 29, 16), 'Hour'),   # still open
            Point('Test Acct', 'Dev', 'Ch', 2.0, _t(2026, 8, 29, 15), 'Hour'),   # closed
        ], nowUTC=now)
        self.assertEqual(written, 1)

    def test_renamed_victoria_metrics_tag_values_are_honoured(self):
        config = self._config(victoriaMetrics={'url': 'http://vm:8428', 'tagValue_hour': 'hourly'})
        now = datetime.datetime(2026, 8, 29, 16, 47, tzinfo=UTC)
        openHour = [Point('Test Acct', 'Dev', 'Ch', 1.0, _t(2026, 8, 29, 16), 'hourly')]
        self.assertEqual(cache.mirrorPoints(config, self.account, openHour, nowUTC=now), 0)
