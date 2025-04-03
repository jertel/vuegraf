# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import copy
import datetime
import influxdb_client
from unittest.mock import MagicMock, patch

# Local imports
from vuegraf import influx
from vuegraf.time import getTimeNow

# Sample config for testing
SAMPLE_CONFIG_V1 = {
    'influxDb': {
        'version': 1,
        'host': 'localhost',
        'port': 8086,
        'database': 'vuegraf',
        'user': 'testuser',
        'pass': 'testpass',
        'ssl_enable': False,
        'ssl_verify': True,
        'tagName': 'detail',
        'tagValue_second': '1s',
        'tagValue_minute': '1m',
        'tagValue_hour': '1h',
        'tagValue_day': '1d'
    },
    'addStationField': False,
    'detailedIntervalSecs': 3600,
    'args': MagicMock(debug=False, dryrun=False, resetdatabase=False)
}

SAMPLE_CONFIG_V2 = {
    'influxDb': {
        'version': 2,
        'url': 'http://localhost:8086',
        'bucket': 'vuegraf',
        'org': 'my-org',
        'token': 'my-token',
        'ssl_verify': True,
        'tagName': 'detail',
        'tagValue_second': '1s',
        'tagValue_minute': '1m',
        'tagValue_hour': '1h',
        'tagValue_day': '1d'
    },
    'addStationField': False,
    'detailedIntervalSecs': 3600,
    'args': MagicMock(debug=False, dryrun=False, resetdatabase=False)
}


# --- Test createDataPoint ---

def test_create_data_point_v1():
    """Test creating a data point for InfluxDB v1."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    timestamp = getTimeNow(datetime.UTC)
    point = influx.createDataPoint(
        config, 'account', 'device', 'channel', 100.5, timestamp, '1m'
    )
    assert point['measurement'] == 'energy_usage'
    assert point['tags']['account_name'] == 'account'
    assert point['tags']['device_name'] == 'channel'
    assert point['tags']['detail'] == '1m'
    assert 'station_name' not in point['tags']
    assert point['fields']['usage'] == 100.5
    assert point['time'] == timestamp


def test_create_data_point_v1_with_station():
    """Test creating a data point for InfluxDB v1 with station field."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['addStationField'] = True
    timestamp = getTimeNow(datetime.UTC)
    point = influx.createDataPoint(
        config, 'account', 'device', 'channel', 100.5, timestamp, '1m'
    )
    assert point['tags']['station_name'] == 'device'


@patch('influxdb_client.Point')
def test_create_data_point_v2(mock_point_class):
    """Test creating a data point for InfluxDB v2."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    timestamp = getTimeNow(datetime.UTC)
    mock_point_instance = MagicMock()
    mock_point_class.return_value = mock_point_instance
    mock_point_instance.tag.return_value = mock_point_instance
    mock_point_instance.field.return_value = mock_point_instance
    mock_point_instance.time.return_value = mock_point_instance

    point = influx.createDataPoint(
        config, 'account', 'device', 'channel', 100.5, timestamp, '1s'
    )

    mock_point_class.assert_called_once_with('energy_usage')
    mock_point_instance.tag.assert_any_call('account_name', 'account')
    mock_point_instance.tag.assert_any_call('device_name', 'channel')
    mock_point_instance.tag.assert_any_call('detail', '1s')
    mock_point_instance.field.assert_called_once_with('usage', 100.5)
    mock_point_instance.time.assert_called_once_with(time=timestamp)
    # Check station_name was NOT called
    assert all(call.args != ('station_name', 'device') for call in mock_point_instance.tag.call_args_list)
    assert point == mock_point_instance


@patch('influxdb_client.Point')
def test_create_data_point_v2_with_station(mock_point_class):
    """Test creating a data point for InfluxDB v2 with station field."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    config['addStationField'] = True
    timestamp = getTimeNow(datetime.UTC)
    mock_point_instance = MagicMock()
    mock_point_class.return_value = mock_point_instance
    mock_point_instance.tag.return_value = mock_point_instance  # Chain calls

    influx.createDataPoint(
        config, 'account', 'device', 'channel', 100.5, timestamp, '1s'
    )

    mock_point_instance.tag.assert_any_call('station_name', 'device')


# --- Test getLastDBTimeStamp ---

@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_no_data_minute(mock_influx_client):
    """Test getLastDBTimeStamp for v1 when no minute data exists."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    mock_influx_client.query.return_value = []  # Simulate no data

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect backfill for 7 days, batched to 12 hours
    expected_start_time = start_time_initial - datetime.timedelta(days=7)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=12)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True
    mock_influx_client.query.assert_called_once()
    assert "device_name = 'channel'" in mock_influx_client.query.call_args[0][0]
    assert "detail = '1m'" in mock_influx_client.query.call_args[0][0]
    assert "station_name" not in mock_influx_client.query.call_args[0][0]


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_no_data_second(mock_influx_client):
    """Test getLastDBTimeStamp for v1 when no second data exists."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    mock_influx_client.query.return_value = []  # Simulate no data

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(minutes=5)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect backfill for 3 hours, batched to 1 hour
    expected_start_time = start_time_initial - datetime.timedelta(hours=3)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=1)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True
    mock_influx_client.query.assert_called_once()
    assert "detail = '1s'" in mock_influx_client.query.call_args[0][0]


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_recent_data_minute(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with recent minute data."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    now = getTimeNow(datetime.UTC)
    last_record_time_str = (now - datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    mock_result = MagicMock()
    mock_result.__len__.return_value = 1  # Simulate one result
    mock_result.get_points.return_value = iter([{'time': last_record_time_str}])
    mock_influx_client.query.return_value = mock_result

    start_time_initial = now - datetime.timedelta(minutes=2)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time to be 1 minute after the last record, stop time unchanged
    expected_start_time = datetime.datetime.strptime(last_record_time_str, '%Y-%m-%dT%H:%M:%SZ')
    expected_start_time = expected_start_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(minutes=1)
    assert start_time == expected_start_time
    assert stop_time == stop_time_initial  # Stop time remains the original 'now'
    assert fill_in_missing_data is True  # Because db time < stopTime - 2 mins


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_old_data_minute_batching(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with old minute data requiring batching."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    now = getTimeNow(datetime.UTC)
    # Last record is 1 day old
    last_record_time_str = (now - datetime.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    mock_result = MagicMock()
    mock_result.__len__.return_value = 1  # Simulate one result
    mock_result.get_points.return_value = iter([{'time': last_record_time_str}])
    mock_influx_client.query.return_value = mock_result

    start_time_initial = now - datetime.timedelta(hours=24)  # Should be ignored
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time 1 min after last record, stop time batched to 12 hours later
    expected_start_time = datetime.datetime.strptime(last_record_time_str, '%Y-%m-%dT%H:%M:%SZ')
    expected_start_time = expected_start_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(minutes=1)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=12)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_very_old_data_minute_7_day_limit(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with very old minute data hitting 7-day limit."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    now = getTimeNow(datetime.UTC)
    # Last record is 10 days old
    last_record_time_str = (now - datetime.timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
    mock_result = MagicMock()
    mock_result.__len__.return_value = 1  # Simulate one result
    mock_result.get_points.return_value = iter([{'time': last_record_time_str}])
    mock_influx_client.query.return_value = mock_result

    start_time_initial = now - datetime.timedelta(days=10)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time to be limited to 7 days ago, stop time batched to 12 hours later
    expected_start_time = stop_time_initial - datetime.timedelta(minutes=10080)  # 7 days
    expected_stop_time = expected_start_time + datetime.timedelta(hours=12)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_recent_data_second(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with recent second data."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    now = getTimeNow(datetime.UTC)
    # Last record 5 seconds ago
    last_record_time_str = (now - datetime.timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    mock_result = MagicMock()
    mock_result.__len__.return_value = 1  # Simulate one result
    mock_result.get_points.return_value = iter([{'time': last_record_time_str}])
    mock_influx_client.query.return_value = mock_result

    start_time_initial = now - datetime.timedelta(seconds=2)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time 1 second after last record, stop time unchanged
    expected_start_time = datetime.datetime.strptime(last_record_time_str, '%Y-%m-%dT%H:%M:%SZ')
    expected_start_time = expected_start_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=1)
    assert start_time == expected_start_time.replace(microsecond=0)  # Microseconds are stripped
    assert stop_time == stop_time_initial
    assert fill_in_missing_data is True  # Because db time < startTime - 2 secs


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_recent_data_second_no_batching(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with recent second data not needing batching."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    now = getTimeNow(datetime.UTC)
    # Last record 30 minutes ago
    last_record_time = now - datetime.timedelta(minutes=30)
    last_record_time_str = last_record_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    mock_result = MagicMock()
    mock_result.__len__.return_value = 1
    mock_result.get_points.return_value = iter([{'time': last_record_time_str}])
    mock_influx_client.query.return_value = mock_result

    start_time_initial = now - datetime.timedelta(minutes=1)  # Should be ignored
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time 1 second after last record, stop time unchanged (no batching)
    expected_start_time = last_record_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=1)
    assert start_time == expected_start_time.replace(microsecond=0)
    assert stop_time == stop_time_initial  # Crucial check: stopTime was not modified by line 137
    assert fill_in_missing_data is True


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_old_data_second_batching(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with old second data requiring batching."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    now = getTimeNow(datetime.UTC)
    # Last record 2 hours old
    last_record_time_str = (now - datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    mock_result = MagicMock()
    mock_result.__len__.return_value = 1  # Simulate one result
    mock_result.get_points.return_value = iter([{'time': last_record_time_str}])
    mock_influx_client.query.return_value = mock_result

    start_time_initial = now - datetime.timedelta(minutes=30)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time 1 sec after last record, stop time batched to 1 hour later
    expected_start_time = datetime.datetime.strptime(last_record_time_str, '%Y-%m-%dT%H:%M:%SZ')
    expected_start_time = expected_start_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=1)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=1)
    assert start_time == expected_start_time.replace(microsecond=0)
    assert stop_time == expected_stop_time.replace(microsecond=0)
    assert fill_in_missing_data is True


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_very_old_data_second_3_hour_limit(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with very old second data hitting 3-hour limit."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    now = getTimeNow(datetime.UTC)
    # Last record 5 hours old
    last_record_time_str = (now - datetime.timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    mock_result = MagicMock()
    mock_result.__len__.return_value = 1  # Simulate one result
    mock_result.get_points.return_value = iter([{'time': last_record_time_str}])
    mock_influx_client.query.return_value = mock_result

    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time limited to 3 hours ago, stop time batched to 1 hour later
    expected_start_time = stop_time_initial - datetime.timedelta(hours=3)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=1)
    assert start_time == expected_start_time.replace(microsecond=0)
    assert stop_time == expected_stop_time.replace(microsecond=0)
    assert fill_in_missing_data is True


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_with_station(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with add_station_field enabled."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['addStationField'] = True
    config['influx'] = mock_influx_client
    mock_influx_client.query.return_value = []  # No data needed for this check

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    influx.getLastDBTimeStamp(
        config, 'device123', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    mock_influx_client.query.assert_called_once()
    query_str = mock_influx_client.query.call_args[0][0]
    assert "station_name = 'device123' AND" in query_str


@patch('influxdb.InfluxDBClient')
def test_get_last_db_timestamp_v1_unsupported_pointtype(mock_influx_client):
    """Test getLastDBTimeStamp for v1 with an unsupported pointType."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influx'] = mock_influx_client
    mock_influx_client.query.return_value = []  # Simulate no data

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False
    unsupported_point_type = 'invalid_type'

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', unsupported_point_type, start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect no changes as the pointType is not supported for backfill logic
    assert start_time == start_time_initial
    assert stop_time == stop_time_initial
    assert fill_in_missing_data == fill_in_missing_data_initial
    mock_influx_client.query.assert_called_once()
    # Verify the query included the unsupported type
    assert f"detail = '{unsupported_point_type}'" in mock_influx_client.query.call_args[0][0]


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_no_data_minute(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 when no minute data exists."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    mock_query_api.query.return_value = []  # Simulate no data
    config['influx'] = mock_influx_instance

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect backfill for 7 days, batched to 12 hours
    expected_start_time = start_time_initial - datetime.timedelta(days=7)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=12)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True
    mock_query_api.query.assert_called_once()
    query_str = mock_query_api.query.call_args[0][0]
    assert 'bucket:"vuegraf"' in query_str
    assert 'r._measurement == "energy_usage"' in query_str
    assert 'r.detail == "1m"' in query_str
    assert 'r.device_name == "channel"' in query_str
    assert 'r.station_name' not in query_str  # Default config


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_no_data_second(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 when no second data exists."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    mock_query_api.query.return_value = []  # Simulate no data
    config['influx'] = mock_influx_instance

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(minutes=5)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect backfill for 3 hours, batched to 1 hour
    expected_start_time = start_time_initial - datetime.timedelta(hours=3)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=1)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True
    mock_query_api.query.assert_called_once()
    query_str = mock_query_api.query.call_args[0][0]
    assert 'r.detail == "1s"' in query_str


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_recent_data_minute(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with recent minute data."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    last_record_time = now - datetime.timedelta(minutes=5)
    mock_record = {}
    mock_record["_time"] = last_record_time
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]  # Simulate data found
    config['influx'] = mock_influx_instance

    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time to be 1 minute after the last record, stop time unchanged
    expected_start_time = last_record_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(minutes=1)
    assert start_time == expected_start_time
    assert stop_time == stop_time_initial  # Stop time remains the original 'now'
    assert fill_in_missing_data is True  # Because db time < stopTime - 2 mins


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_old_data_minute_batching(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with old minute data requiring batching."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    # Last record is 1 day old
    last_record_time = now - datetime.timedelta(days=1)
    mock_record = {}
    mock_record["_time"] = last_record_time
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]
    config['influx'] = mock_influx_instance

    start_time_initial = now - datetime.timedelta(hours=24)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time 1 min after last record, stop time batched to 12 hours later
    expected_start_time = last_record_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(minutes=1)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=12)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_very_old_data_minute_7_day_limit(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with very old minute data hitting 7-day limit."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    # Last record is 10 days old
    last_record_time = now - datetime.timedelta(days=10)
    mock_record = {}
    mock_record["_time"] = last_record_time
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]
    config['influx'] = mock_influx_instance

    start_time_initial = now - datetime.timedelta(days=10)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time to be limited to 7 days ago, stop time batched to 12 hours later
    expected_start_time = stop_time_initial - datetime.timedelta(minutes=10080)  # 7 days
    expected_stop_time = expected_start_time + datetime.timedelta(hours=12)
    assert start_time == expected_start_time
    assert stop_time == expected_stop_time
    assert fill_in_missing_data is True


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_recent_data_second(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with recent second data."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    # Last record 5 seconds ago
    last_record_time = now - datetime.timedelta(seconds=5)
    mock_record = {}
    mock_record["_time"] = last_record_time
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]
    config['influx'] = mock_influx_instance

    start_time_initial = now - datetime.timedelta(seconds=2)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time 1 second after last record, stop time unchanged
    expected_start_time = last_record_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=1)
    assert start_time == expected_start_time.replace(microsecond=0)  # Microseconds are stripped
    assert stop_time == stop_time_initial
    assert fill_in_missing_data is True  # Because db time < startTime - 2 secs


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_recent_1m_data(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with recent second data not needing batching."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    # Last record 1 minutes ago
    last_record_time = now - datetime.timedelta(minutes=1)
    mock_record = {"_time": last_record_time}
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]  # Simulate data found
    config['influx'] = mock_influx_instance

    start_time_initial = now
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    assert fill_in_missing_data is False


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_recent_1s_data(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with recent second data not needing batching."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    # Last record 1 minutes ago
    last_record_time = now - datetime.timedelta(seconds=1)
    mock_record = {"_time": last_record_time}
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]  # Simulate data found
    config['influx'] = mock_influx_instance

    start_time_initial = now
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    assert fill_in_missing_data is False


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_old_data_second_batching(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with old second data requiring batching."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    config['detailedIntervalSecs'] = 1800
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    # Last record 2 hours old
    last_record_time = now - datetime.timedelta(hours=2)
    mock_record = {}
    mock_record["_time"] = last_record_time
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]
    config['influx'] = mock_influx_instance

    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time 1 sec after last record, stop time batched to 1 hour later
    expected_start_time = last_record_time.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=1)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=1)
    assert start_time == expected_start_time.replace(microsecond=0)
    assert stop_time == expected_stop_time.replace(microsecond=0)
    assert fill_in_missing_data is True


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_very_old_data_second_3_hour_limit(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with very old second data hitting 3-hour limit."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    config['detailedIntervalSecs'] = 7200
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    now = getTimeNow(datetime.UTC)
    # Last record 5 hours old
    last_record_time = now - datetime.timedelta(hours=5)
    mock_record = {}
    mock_record["_time"] = last_record_time
    mock_table = MagicMock()
    mock_table.records = [mock_record]
    mock_query_api.query.return_value = [mock_table]
    config['influx'] = mock_influx_instance

    start_time_initial = now - datetime.timedelta(hours=4)  # Last record is older than our start time
    stop_time_initial = now
    fill_in_missing_data_initial = False

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', '1s', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect start time limited to 1 hours ago due to detailedIntervalSecs>=3600
    expected_start_time = stop_time_initial - datetime.timedelta(hours=1)
    expected_stop_time = expected_start_time + datetime.timedelta(hours=1)
    assert start_time == expected_start_time.replace(microsecond=0)
    assert stop_time == expected_stop_time.replace(microsecond=0)
    assert fill_in_missing_data is True


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_with_station(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with add_station_field enabled."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    config['addStationField'] = True
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    mock_query_api.query.return_value = []  # No data needed for this check
    config['influx'] = mock_influx_instance

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False

    influx.getLastDBTimeStamp(
        config, 'device123', 'channel', '1m', start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    mock_query_api.query.assert_called_once()
    query_str = mock_query_api.query.call_args[0][0]
    assert 'r.station_name == "device123"' in query_str


@patch('influxdb_client.InfluxDBClient')
def test_get_last_db_timestamp_v2_unsupported_pointtype(mock_influx_client_class):
    """Test getLastDBTimeStamp for v2 with an unsupported pointType."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_query_api = MagicMock()
    mock_influx_instance.query_api.return_value = mock_query_api
    mock_query_api.query.return_value = []  # Simulate no data
    config['influx'] = mock_influx_instance

    now = getTimeNow(datetime.UTC)
    start_time_initial = now - datetime.timedelta(hours=1)
    stop_time_initial = now
    fill_in_missing_data_initial = False
    unsupported_point_type = 'invalid_type'

    start_time, stop_time, fill_in_missing_data = influx.getLastDBTimeStamp(
        config, 'device', 'channel', unsupported_point_type, start_time_initial, stop_time_initial, fill_in_missing_data_initial
    )

    # Expect no changes as the pointType is not supported for backfill logic
    assert start_time == start_time_initial
    assert stop_time == stop_time_initial
    assert fill_in_missing_data == fill_in_missing_data_initial
    mock_query_api.query.assert_called_once()
    # Verify the query included the unsupported type
    query_str = mock_query_api.query.call_args[0][0]
    assert f'r.detail == "{unsupported_point_type}"' in query_str


# --- Test initInfluxConnection ---

@patch('influxdb.InfluxDBClient')
def test_init_influx_connection_v1_with_auth(mock_influx_client_class):
    """Test initInfluxConnection for v1 with authentication."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    mock_influx_instance = MagicMock()
    mock_influx_client_class.return_value = mock_influx_instance

    influx.initInfluxConnection(config)

    mock_influx_client_class.assert_called_once_with(
        host='localhost',
        port=8086,
        username='testuser',
        password='testpass',
        database='vuegraf',
        ssl=False,
        verify_ssl=True
    )
    mock_influx_instance.create_database.assert_called_once_with('vuegraf')
    mock_influx_instance.delete_series.assert_not_called()
    assert config['influx'] == mock_influx_instance


@patch('influxdb.InfluxDBClient')
def test_init_influx_connection_v1_no_auth(mock_influx_client_class):
    """Test initInfluxConnection for v1 without authentication."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    del config['influxDb']['user']
    del config['influxDb']['pass']
    mock_influx_instance = MagicMock()
    mock_influx_client_class.return_value = mock_influx_instance

    influx.initInfluxConnection(config)

    mock_influx_client_class.assert_called_once_with(
        host='localhost',
        port=8086,
        database='vuegraf',
        ssl=False,
        verify_ssl=True
    )
    mock_influx_instance.create_database.assert_called_once_with('vuegraf')
    assert config['influx'] == mock_influx_instance


@patch('influxdb.InfluxDBClient')
def test_init_influx_connection_v1_missing_ssl_config(mock_influx_client_class):
    """Test initInfluxConnection for v1 without authentication."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    del config['influxDb']['user']
    del config['influxDb']['pass']
    del config['influxDb']['ssl_enable']
    del config['influxDb']['ssl_verify']
    mock_influx_instance = MagicMock()
    mock_influx_client_class.return_value = mock_influx_instance

    influx.initInfluxConnection(config)

    mock_influx_client_class.assert_called_once_with(
        host='localhost',
        port=8086,
        database='vuegraf',
        ssl=False,
        verify_ssl=True
    )
    mock_influx_instance.create_database.assert_called_once_with('vuegraf')
    assert config['influx'] == mock_influx_instance


@patch('influxdb.InfluxDBClient')
def test_init_influx_connection_v1_ssl(mock_influx_client_class):
    """Test initInfluxConnection for v1 with SSL enabled."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['influxDb']['ssl_enable'] = True
    config['influxDb']['ssl_verify'] = False  # Test verify false as well
    mock_influx_instance = MagicMock()
    mock_influx_client_class.return_value = mock_influx_instance

    influx.initInfluxConnection(config)

    mock_influx_client_class.assert_called_once_with(
        host='localhost',
        port=8086,
        username='testuser',
        password='testpass',
        database='vuegraf',
        ssl=True,
        verify_ssl=False
    )
    assert config['influx'] == mock_influx_instance


@patch('influxdb.InfluxDBClient')
def test_init_influx_connection_v1_reset(mock_influx_client_class):
    """Test initInfluxConnection for v1 with resetdatabase flag."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['args'] = MagicMock(debug=False, dryrun=False, resetdatabase=True)
    mock_influx_instance = MagicMock()
    mock_influx_client_class.return_value = mock_influx_instance

    influx.initInfluxConnection(config)

    mock_influx_instance.create_database.assert_called_once_with('vuegraf')
    mock_influx_instance.delete_series.assert_called_once_with(measurement='energy_usage')
    assert config['influx'] == mock_influx_instance


@patch('influxdb_client.InfluxDBClient')
@patch('vuegraf.influx.getTimeNow')
def test_init_influx_connection_v2(mock_get_time_now, mock_influx_client_class):
    """Test initInfluxConnection for v2."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_influx_client_class.return_value = mock_influx_instance
    mock_delete_api = MagicMock()
    mock_influx_instance.delete_api.return_value = mock_delete_api
    # Mock time for reset check
    fixed_now = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    mock_get_time_now.return_value = fixed_now

    influx.initInfluxConnection(config)

    mock_influx_client_class.assert_called_once_with(
        url='http://localhost:8086',
        token='my-token',
        org='my-org',
        verify_ssl=True
    )
    mock_influx_instance.delete_api.assert_not_called()
    assert config['influx'] == mock_influx_instance


@patch('influxdb_client.InfluxDBClient')
@patch('vuegraf.influx.getTimeNow')
def test_init_influx_connection_v2_reset(mock_get_time_now, mock_influx_client_class):
    """Test initInfluxConnection for v2 with resetdatabase flag."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    config['args'] = MagicMock(debug=False, dryrun=False, resetdatabase=True)
    mock_influx_instance = MagicMock()
    mock_influx_client_class.return_value = mock_influx_instance
    mock_delete_api = MagicMock()
    mock_influx_instance.delete_api.return_value = mock_delete_api
    # Mock time for reset check
    fixed_now = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    mock_get_time_now.return_value = fixed_now
    expected_stop_time = '2024-01-01T12:00:00Z'

    influx.initInfluxConnection(config)

    mock_influx_client_class.assert_called_once()  # Already checked in previous test
    mock_influx_instance.delete_api.assert_called_once()
    mock_delete_api.delete.assert_called_once_with(
        '1970-01-01T00:00:00Z',
        expected_stop_time,
        '_measurement="energy_usage"',
        bucket='vuegraf',
        org='my-org'
    )
    assert config['influx'] == mock_influx_instance


# --- Test writeInfluxPoints ---

@patch('vuegraf.influx.dumpPoints')
@patch('influxdb.InfluxDBClient')
def test_write_influx_points_v1(mock_influx_client_class, mock_dump_points):
    """Test writeInfluxPoints for v1."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    mock_influx_instance = MagicMock()
    config['influx'] = mock_influx_instance
    points = [{'measurement': 'test', 'fields': {'value': 1}}]

    influx.writeInfluxPoints(config, points)

    mock_influx_instance.write_points.assert_called_once_with(points, batch_size=5000)
    mock_dump_points.assert_not_called()


@patch('vuegraf.influx.dumpPoints')
@patch('influxdb_client.InfluxDBClient')
@patch('influxdb_client.client.write_api.SYNCHRONOUS', 'SYNCHRONOUS')  # Mock the constant
def test_write_influx_points_v2(mock_influx_client_class, mock_dump_points):
    """Test writeInfluxPoints for v2."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_influx_instance = MagicMock()
    mock_write_api = MagicMock()
    mock_influx_instance.write_api.return_value = mock_write_api
    config['influx'] = mock_influx_instance
    # Create mock Point objects for v2
    mock_point1 = MagicMock(spec=influxdb_client.Point)
    mock_point2 = MagicMock(spec=influxdb_client.Point)
    points = [mock_point1, mock_point2]

    influx.writeInfluxPoints(config, points)

    mock_influx_instance.write_api.assert_called_once_with(write_options='SYNCHRONOUS')
    mock_write_api.write.assert_called_once_with(bucket='vuegraf', record=points)
    mock_dump_points.assert_not_called()


@patch('vuegraf.influx.dumpPoints')
@patch('influxdb.InfluxDBClient')
@patch('influxdb_client.InfluxDBClient')
def test_write_influx_points_dryrun(mock_influx_v2, mock_influx_v1, mock_dump_points):
    """Test writeInfluxPoints with dryrun enabled."""
    # Test V1 dryrun
    config_v1 = copy.deepcopy(SAMPLE_CONFIG_V1)
    config_v1['args'] = MagicMock(debug=False, dryrun=True, resetdatabase=False)
    mock_influx_instance_v1 = MagicMock()
    config_v1['influx'] = mock_influx_instance_v1
    points_v1 = [{'measurement': 'test', 'fields': {'value': 1}}]
    influx.writeInfluxPoints(config_v1, points_v1)
    mock_influx_instance_v1.write_points.assert_not_called()

    # Test V2 dryrun
    config_v2 = copy.deepcopy(SAMPLE_CONFIG_V2)
    config_v2['args'] = MagicMock(debug=False, dryrun=True, resetdatabase=False)
    mock_influx_instance_v2 = MagicMock()
    mock_write_api_v2 = MagicMock()
    mock_influx_instance_v2.write_api.return_value = mock_write_api_v2
    config_v2['influx'] = mock_influx_instance_v2
    points_v2 = [MagicMock(spec=influxdb_client.Point)]
    influx.writeInfluxPoints(config_v2, points_v2)
    mock_influx_instance_v2.write_api.assert_not_called()
    mock_write_api_v2.write.assert_not_called()

    mock_dump_points.assert_not_called()  # dumpPoints not called in dryrun unless debug is also true


@patch('vuegraf.influx.dumpPoints')
@patch('influxdb.InfluxDBClient')
def test_write_influx_points_debug(mock_influx_client_class, mock_dump_points):
    """Test writeInfluxPoints with debug enabled."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    config['args'] = MagicMock(debug=True, dryrun=False, resetdatabase=False)  # Enable debug
    mock_influx_instance = MagicMock()
    config['influx'] = mock_influx_instance
    points = [{'measurement': 'test', 'fields': {'value': 1}}]

    influx.writeInfluxPoints(config, points)

    mock_influx_instance.write_points.assert_called_once_with(points, batch_size=5000)
    mock_dump_points.assert_called_once_with(config, "Sending to database", points)


# --- Test dumpPoints ---

@patch('vuegraf.influx.logger')
@patch('pprint.pformat')
def test_dump_points_v1(mock_pformat, mock_logger):
    """Test dumpPoints for v1."""
    config = copy.deepcopy(SAMPLE_CONFIG_V1)
    points = [
        {'measurement': 'm1', 'fields': {'f1': 1}},
        {'measurement': 'm2', 'tags': {'t1': 'v1'}, 'fields': {'f2': 2.0}}
    ]
    mock_pformat.side_effect = lambda p: f"formatted_{p['measurement']}"  # Simulate pformat output

    influx.dumpPoints(config, "Test Label V1", points)

    mock_logger.debug.assert_any_call("Test Label V1")
    mock_logger.debug.assert_any_call("  formatted_m1")
    mock_logger.debug.assert_any_call("  formatted_m2")
    assert mock_logger.debug.call_count == 3
    mock_pformat.assert_any_call(points[0])
    mock_pformat.assert_any_call(points[1])


@patch('vuegraf.influx.logger')
def test_dump_points_v2(mock_logger):
    """Test dumpPoints for v2."""
    config = copy.deepcopy(SAMPLE_CONFIG_V2)
    mock_point1 = MagicMock(spec=influxdb_client.Point)
    mock_point1.to_line_protocol.return_value = "point1_line_protocol"
    mock_point2 = MagicMock(spec=influxdb_client.Point)
    mock_point2.to_line_protocol.return_value = "point2_line_protocol"
    points = [mock_point1, mock_point2]

    influx.dumpPoints(config, "Test Label V2", points)

    mock_logger.debug.assert_any_call("Test Label V2")
    mock_logger.debug.assert_any_call("  point1_line_protocol")
    mock_logger.debug.assert_any_call("  point2_line_protocol")
    assert mock_logger.debug.call_count == 3
    mock_point1.to_line_protocol.assert_called_once()
    mock_point2.to_line_protocol.assert_called_once()
