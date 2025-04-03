# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import datetime
from unittest.mock import patch
import pytest
import pytz

# Local imports
from vuegraf import time

# Sample config for testing
SAMPLE_CONFIG_VALID_TZ = {'timezone': 'America/New_York'}
SAMPLE_CONFIG_NO_TZ = {}
SAMPLE_CONFIG_INVALID_TZ = {'timezone': 'Invalid/Timezone'}


# --- Tests for getTimezone ---

def test_getTimezone_valid():
    """Test getTimezone with a valid timezone string."""
    with patch('vuegraf.time.getConfigValue', return_value='America/New_York') as mock_get_config:
        tz = time.getTimezone(SAMPLE_CONFIG_VALID_TZ)
        mock_get_config.assert_called_once_with(SAMPLE_CONFIG_VALID_TZ, 'timezone')
        assert isinstance(tz, pytz.tzinfo.DstTzInfo)
        assert tz.zone == 'America/New_York'


def test_getTimezone_none():
    """Test getTimezone when timezone is not configured."""
    with patch('vuegraf.time.getConfigValue', return_value=None) as mock_get_config:
        tz = time.getTimezone(SAMPLE_CONFIG_NO_TZ)
        mock_get_config.assert_called_once_with(SAMPLE_CONFIG_NO_TZ, 'timezone')
        assert tz is None


def test_getTimezone_invalid():
    """Test getTimezone with an invalid timezone string."""
    with patch('vuegraf.time.getConfigValue', return_value='Invalid/Timezone') as mock_get_config:
        with pytest.raises(pytz.UnknownTimeZoneError):
            time.getTimezone(SAMPLE_CONFIG_INVALID_TZ)
        mock_get_config.assert_called_once_with(SAMPLE_CONFIG_INVALID_TZ, 'timezone')


# --- Tests for getCurrentHourUTC ---

@patch('vuegraf.time.getTimeNow')
def test_getCurrentHourUTC(mock_getTimeNow):
    """Test getCurrentHourUTC."""
    mock_now = datetime.datetime(2024, 4, 1, 10, 30, 45, tzinfo=datetime.UTC)
    mock_getTimeNow.return_value = mock_now
    expected_hour = datetime.datetime(2024, 4, 1, 10, 0, 0, tzinfo=datetime.UTC)

    result = time.getCurrentHourUTC()

    mock_getTimeNow.assert_called_once_with(datetime.UTC)
    assert result == expected_hour


# --- Tests for getCurrentDayLocal ---

@patch('vuegraf.time.getTimeNow')
@patch('vuegraf.time.getTimezone', return_value=pytz.timezone('America/New_York'))
def test_getCurrentDayLocal(mock_getTimezone, mock_getTimeNow):
    """Test getCurrentDayLocal."""
    mock_now_local = datetime.datetime(2024, 4, 1, 10, 30, 45, tzinfo=pytz.timezone('America/New_York'))
    mock_getTimeNow.return_value = mock_now_local
    expected_day_end = datetime.datetime(2024, 4, 1, 23, 59, 59, tzinfo=pytz.timezone('America/New_York'))

    result = time.getCurrentDayLocal(SAMPLE_CONFIG_VALID_TZ)

    mock_getTimezone.assert_called_once_with(SAMPLE_CONFIG_VALID_TZ)
    mock_getTimeNow.assert_called_once_with(pytz.timezone('America/New_York'))
    assert result == expected_day_end


# --- Tests for convertToLocalDayInUTC ---

@patch('vuegraf.time.getTimezone', return_value=pytz.timezone('America/New_York'))
def test_convertToLocalDayInUTC(mock_getTimezone):
    """Test convertToLocalDayInUTC."""
    # Input timestamp in UTC
    input_ts_utc = datetime.datetime(2024, 4, 1, 10, 30, 45, tzinfo=pytz.UTC)
    # Expected end of day in local time (America/New_York is UTC-4 during DST)
    # 2024-04-01 23:59:59 EDT is 2024-04-02 03:59:59 UTC
    expected_ts_utc = datetime.datetime(2024, 4, 2, 3, 59, 59, tzinfo=pytz.UTC)

    result = time.convertToLocalDayInUTC(SAMPLE_CONFIG_VALID_TZ, input_ts_utc)

    mock_getTimezone.assert_called_once_with(SAMPLE_CONFIG_VALID_TZ)
    assert result == expected_ts_utc


# --- Tests for calculateHistoryTimeRange ---

@patch('vuegraf.time.getTimezone', return_value=pytz.timezone('America/New_York'))
def test_calculateHistoryTimeRange_basic(mock_getTimezone):
    """Test calculateHistoryTimeRange basic functionality."""
    config = SAMPLE_CONFIG_VALID_TZ
    # Simulate 'now' minus lag
    now_lag_utc = datetime.datetime(2024, 4, 20, 12, 0, 0, tzinfo=pytz.UTC)
    # Start time for the *first* increment (before applying historyIncrements)
    start_time_utc = datetime.datetime(2024, 3, 1, 6, 24, 23, tzinfo=pytz.UTC)
    history_increments = 0  # First increment

    # Expected start: 2024-03-01 00:00:00 EST (UTC-5) -> 2024-03-01 05:00:00 UTC
    expected_start_utc = datetime.datetime(2024, 3, 1, 5, 0, 0, tzinfo=pytz.UTC)
    # Expected stop: 20 days later (Mar 20), end of day EST (UTC-5) -> 2024-03-21 04:59:59 UTC
    # Note: DST starts March 10, 2024 in America/New_York
    # Start: 2024-03-01 00:00:00 EST
    # Stop: 2024-03-20 23:59:59 EDT (UTC-4) -> 2024-03-21 03:59:59 UTC
    expected_stop_utc = datetime.datetime(2024, 3, 21, 3, 59, 59, tzinfo=pytz.UTC)

    start_result, stop_result = time.calculateHistoryTimeRange(config, now_lag_utc, start_time_utc, history_increments)

    mock_getTimezone.assert_called()  # Called multiple times within the function
    assert start_result == expected_start_utc
    assert stop_result == expected_stop_utc


@patch('vuegraf.time.getTimezone', return_value=pytz.timezone('America/New_York'))
def test_calculateHistoryTimeRange_increment(mock_getTimezone):
    """Test calculateHistoryTimeRange with historyIncrements > 0."""
    config = SAMPLE_CONFIG_VALID_TZ
    now_lag_utc = datetime.datetime(2024, 4, 20, 12, 0, 0, tzinfo=pytz.UTC)
    start_time_utc = datetime.datetime(2024, 3, 1, 6, 24, 23, tzinfo=pytz.UTC)
    history_increments = 1  # Second increment (20 days later)

    # Base start: 2024-03-01 UTC
    # Increment start: 2024-03-01 + 20 days = 2024-03-21 UTC
    # Start of local day (EDT, UTC-4): 2024-03-21 00:00:00 EDT -> 2024-03-21 04:00:00 UTC
    expected_start_utc = datetime.datetime(2024, 3, 21, 4, 0, 0, tzinfo=pytz.UTC)
    # Stop: Start + 19 days = 2024-04-09
    # End of local day (EDT, UTC-4): 2024-04-09 23:59:59 EDT -> 2024-04-10 03:59:59 UTC
    expected_stop_utc = datetime.datetime(2024, 4, 10, 3, 59, 59, tzinfo=pytz.UTC)

    start_result, stop_result = time.calculateHistoryTimeRange(config, now_lag_utc, start_time_utc, history_increments)

    assert start_result == expected_start_utc
    assert stop_result == expected_stop_utc


@patch('vuegraf.time.getTimezone', return_value=pytz.timezone('America/New_York'))
def test_calculateHistoryTimeRange_stop_limited_by_now(mock_getTimezone):
    """Test calculateHistoryTimeRange when stop time is limited by nowLagUTC."""
    config = SAMPLE_CONFIG_VALID_TZ
    # Set 'now' earlier, so it limits the stop time
    now_lag_utc = datetime.datetime(2024, 3, 15, 12, 0, 0, tzinfo=pytz.UTC)
    start_time_utc = datetime.datetime(2024, 3, 1, 6, 24, 23, tzinfo=pytz.UTC)
    history_increments = 0

    # Expected start: 2024-03-01 00:00:00 EST (UTC-5) -> 2024-03-01 05:00:00 UTC
    expected_start_utc = datetime.datetime(2024, 3, 1, 5, 0, 0, tzinfo=pytz.UTC)
    # Expected stop (calculated): 2024-03-21 03:59:59 UTC
    # Expected stop (limited by now_lag_utc): 2024-03-15 12:00:00 UTC
    expected_stop_utc = now_lag_utc

    start_result, stop_result = time.calculateHistoryTimeRange(config, now_lag_utc, start_time_utc, history_increments)

    assert start_result == expected_start_utc
    assert stop_result == expected_stop_utc
