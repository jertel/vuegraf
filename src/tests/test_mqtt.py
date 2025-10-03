# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import copy
import datetime
from unittest.mock import patch
import pytest

# Local imports
from vuegraf import mqtt
from vuegraf.collect import Point

TIMESTAMP = datetime.datetime(2024, 1, 10, 12, 0, 30, tzinfo=datetime.timezone.utc)

# Minimal valid config for MQTT
CONFIG = {
  "mqtt": {
    "host": "unittest.mqtt.host",
  }
}


def test_init_config_noop_if_absent():
  config = {}
  mqtt.initMqttConnectionIfConfigured(config)
  assert "mqtt" not in config


def test_init_config_error_missing_host():
  config = {
    "mqtt": {
      "port": 1,
    }
  }
  with pytest.raises(ValueError):
    mqtt.initMqttConnectionIfConfigured(config)


def test_init_config_error_missing_pw():
  config = {
    "mqtt": {
      "host": "unittest.mqtt.host",
      "username": "unittest_user",
    }
  }
  with pytest.raises(ValueError):
    mqtt.initMqttConnectionIfConfigured(config)


@patch('paho.mqtt.client.Client')
def test_init_config_minimal_valid(mock_client_cls):
  config = copy.deepcopy(CONFIG)
  mqtt.initMqttConnectionIfConfigured(config)
  mqttc = config["mqtt"]["client"]
  mqttc.connect.assert_called_once()


@patch('paho.mqtt.client.Client')
def test_init_config_complex_valid(mock_client_cls):
  config = {
    "mqtt": {
      "host": "unittest.mqtt.host",
      "port": 123,
      "username": "unittest_user",
      "password": "unittest_pw",
    }
  }
  mqtt.initMqttConnectionIfConfigured(config)
  mqttc = config["mqtt"]["client"]
  mqttc.username_pw_set.assert_called_once_with("unittest_user", "unittest_pw")
  mqttc.connect.assert_called_once_with("unittest.mqtt.host", port=123)


def test_retain_only_latest_keeps_single():
  """Sanity check that a single Point makes it through."""
  point = Point("account", "device", "chan", 9.0, TIMESTAMP, "Minute")
  retained = mqtt._retainOnlyLatestPointPerChannel([point])
  assert retained == [point]


def test_retain_only_latest_keeps_latest_in_one_channel():
  """Makes sure the max by timestamp is working right."""
  early = Point("account", "device", "chan", 9.0, TIMESTAMP, "Minute")
  mid = Point("account", "device", "chan", 9.0, TIMESTAMP + datetime.timedelta(hours=1), "Minute")
  late = Point("account", "device", "chan", 9.0, TIMESTAMP + datetime.timedelta(hours=2), "Minute")
  retained = mqtt._retainOnlyLatestPointPerChannel([early, late, mid])
  assert retained == [late]


def test_retain_only_latest_keeps_unique_channels():
  """Makes sure the definition of what makes a unique channel is right."""
  retained = mqtt._retainOnlyLatestPointPerChannel([
    Point("account", "device", "chan", 9.0, TIMESTAMP, "Minute"),
    Point("account2", "device", "chan", 9.0, TIMESTAMP, "Minute"),
    Point("account", "device2", "chan", 9.0, TIMESTAMP, "Minute"),
    Point("account", "device", "chan2", 9.0, TIMESTAMP, "Minute"),
  ])
  assert len(retained) == 4


def test_retain_only_latest_keeps_latest_in_two_channels():
  """Makes sure max is correctly taken across two channels."""
  early_a = Point("account", "device", "chan_a", 9.0, TIMESTAMP, "Minute")
  late_a = Point("account", "device", "chan_a", 9.0, TIMESTAMP + datetime.timedelta(hours=2), "Minute")
  early_b = Point("account", "device", "chan_b", 9.0, TIMESTAMP, "Minute")
  late_b = Point("account", "device", "chan_b", 9.0, TIMESTAMP + datetime.timedelta(hours=2), "Minute")
  retained = mqtt._retainOnlyLatestPointPerChannel([early_a, late_a, early_b, late_b])
  assert len(retained) == 2
  assert late_a in retained
  assert late_b in retained


def test_publish_no_error_if_not_connected():
  point = Point("account", "device", "chan_a", 9.0, TIMESTAMP, "Minute")
  mqtt.publishMqttMessagesIfConnected({"mqtt": {}}, [point])


@pytest.mark.parametrize("station_field,topic", [(True, None), (False, None), (False, "custom/topic")])
@patch('paho.mqtt.client.Client')
def test_publish_converts_point(mock_client_class, station_field, topic):
  config = copy.deepcopy(CONFIG)
  config["addStationField"] = station_field
  if topic is not None:
    config["mqtt"]["topic"] = topic
  mqtt.initMqttConnectionIfConfigured(config)
  point = Point("account", "device", "chan", 9.0, TIMESTAMP, "Minute")

  mqtt.publishMqttMessagesIfConnected(config, [point])

  mqttc = config["mqtt"]["client"]
  mqttc.publish.assert_called_once_with(
    topic or "vuegraf/energy_usage",
    '{"account": "account", "device_name": "chan", "usage_watts": 9.0, "epoch_s": 1704888030, "detailed": "Minute"'
    + (', "station": "device"' if station_field else '')
    + '}',
  )


@patch('paho.mqtt.client.Client')
def test_publish_converts_point_and_drops_dup(mock_client_class):
  """Exercises the branch for logging when a point is filtered out.

  This is for pytets coverage of branches. Filtering itself is tested separately,
  but this covers the resulting logic in the publish function.
  """
  config = copy.deepcopy(CONFIG)
  config["addStationField"] = False
  mqtt.initMqttConnectionIfConfigured(config)
  early = Point("account", "device", "chan", 9.0, TIMESTAMP - datetime.timedelta(minutes=1), "Minute")
  late = Point("account", "device", "chan", 9.0, TIMESTAMP, "Minute")

  mqtt.publishMqttMessagesIfConnected(config, [early, late])

  mqttc = config["mqtt"]["client"]
  mqttc.publish.assert_called_once()


def test_disconnect_no_error_if_not_connected():
  mqtt.stopMqttIfConnected({"mqtt": {}})


@patch('paho.mqtt.client.Client')
def test_disconnect_called_if_connected(mock_client_class):
  config = copy.deepcopy(CONFIG)
  mqtt.initMqttConnectionIfConfigured(config)
  mqtt.stopMqttIfConnected(config)
  mqttc = config["mqtt"]["client"]
  mqttc.disconnect.assert_called_once()
