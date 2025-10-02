"""Send Vue usage data to an MQTT pubsub.

Uses the Eclipse paho-mqtt client:
https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
"""
import json
import logging
from paho.mqtt import client

from vuegraf.config import getConfigValue

logger = logging.getLogger('vuegraf.mqtt')


def initMqttConnectionIfConfigured(config) -> None:
  mqtt_config = config.get("mqtt", {})
  if not mqtt_config:
    logger.info("No MQTT client config, skipping setup.")
    return
  mqtt_host = mqtt_config.get("host")
  if not mqtt_host:
    raise ValueError("Missing required \"host\" key within MQTT section.")
  port = mqtt_config.get("port", 1883)
  username = mqtt_config.get("username")
  password = mqtt_config.get("pw")
  if bool(username) != bool(password):
    raise ValueError(
        "MQTT config section contains one of username/pw but not the other."
    )
  topic = mqtt_config.get("topic")
  if not topic:
    topic = "vuegraf/energy_usage"
    # Write back to the config so the publish call has access.
    mqtt_config["topic"] = topic

  mqttc = client.Client(client_id="vuegraf")
  mqttc.enable_logger()
  if username:
    mqttc.username_pw_set(username, password)
  mqttc.connect(mqtt_host, port=port)
  # Start a background thread to run the MQTT network loop.
  mqttc.loop_start()
  mqtt_config["client"] = mqttc
  logger.info(f"MQTT client set up to publish to {mqtt_host} on {topic}.")


def publishMqttMessagesIfConnected(config, usageDataPoints) -> None:
  """Publishes usage value message to MQTT from collect.Point value list."""
  mqttc = config.get("mqtt", {}).get("client")
  if not mqttc:
    logger.debug("No MQTT client configured, skipping publish.")
    return
  topic = config["mqtt"]["topic"]

  addStationField = getConfigValue(config, "addStationField")

  # Use the default fire-and-forget QOS of 0, since we expect to send frequently
  # as Vue power values are updated.
  msg_infos = []
  for pt in usageDataPoints:
    message = {
      "account": pt.accountName,
      "device_name": pt.chanName,
      "usage_watts": pt.usageWatts,
      "epoch_s": pt.timestamp.timestamp(),  # epoch seconds
      "detailed": pt.detailed,
    }
    if addStationField:
      message["station"] = pt.deviceName
    msg_infos.append(mqttc.publish(topic, json.dumps(message)))
  for msg_info in msg_infos:
    msg_info.wait_for_publish()


def stopMqttIfConnected(config) -> None:
  mqttc = config.get("mqtt", {}).get("client")
  if not mqttc:
    logger.info("No MQTT client configured, skipping disconnect.")
    return

  mqttc.disconnect()  # also stops background thread
  logger.info("MQTT client disconnected.")
