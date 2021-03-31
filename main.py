# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0.

import argparse
from awscrt import io, mqtt, auth, http
from awsiot import mqtt_connection_builder
import sys
import threading
import time
import json
from datetime import datetime, timedelta
from astral import LocationInfo
from astral.sun import sun

from board import SCL, SDA
import busio
from adafruit_seesaw.seesaw import Seesaw
from gpiozero import DigitalOutputDevice

parser = argparse.ArgumentParser(description="Send and receive messages through and MQTT connection.")
parser.add_argument('--endpoint', required=True, help="Your AWS IoT custom endpoint, not including a port. " +
                            "Ex: \"abcd123456wxyz-ats.iot.us-east-1.amazonaws.com\"")
parser.add_argument('--cert', help="File path to your client certificate, in PEM format.")
parser.add_argument('--key', help="File path to your private key, in PEM format.")
parser.add_argument('--root-ca', help="File path to root certificate authority, in PEM format. " +
                    "Necessary if MQTT server uses a certificate that's not already in " +
                    "your trust store.")
parser.add_argument('--client-id', default="gardenClient", help="Client ID for MQTT connection.")
parser.add_argument('--city', required=True, help="City name to determine sunrise and sunset times. Ex: New York")
parser.add_argument('--region', required=True, help="Region name to determine sunrise and sunset times. Ex: United States")
parser.add_argument('--timezone', required=True, help="IANA timezone name to determine sunrise and sunset times. Ex: America/New_York")
parser.add_argument('--lat', required=True, help="Latitude to determine sunrise and sunset times. Ex: 40.725380")
parser.add_argument('--long', required=True, help="Longitude to determine sunrise and sunset times. Ex: -73.980760")
parser.add_argument('--verbosity', choices=[x.name for x in io.LogLevel], default=io.LogLevel.NoLogs.name,
  help='Logging level')

args = parser.parse_args()

io.init_logging(getattr(io.LogLevel, args.verbosity), 'stderr')

# Initialize I2C soil sensor
i2c_bus = busio.I2C(SCL, SDA)
soil = Seesaw(i2c_bus, addr=0x36)

# Initialize relay output devices
light_relay = DigitalOutputDevice(17)
pump_relay = DigitalOutputDevice(23)

# Callback when connection is accidentally lost.
def on_connection_interrupted(connection, error, **kwargs):
  print("Connection interrupted. error: {}".format(error))

# Callback when an interrupted connection is re-established.
def on_connection_resumed(connection, return_code, session_present, **kwargs):
  print("Connection resumed. return_code: {} session_present: {}".format(return_code, session_present))
  if return_code == mqtt.ConnectReturnCode.ACCEPTED and not session_present:
    print("Session did not persist. Resubscribing to existing topics...")
    resubscribe_future, _ = connection.resubscribe_existing_topics()
    resubscribe_future.add_done_callback(on_resubscribe_complete)

def on_resubscribe_complete(resubscribe_future):
    resubscribe_results = resubscribe_future.result()
    print("Resubscribe results: {}".format(resubscribe_results))
    for topic, qos in resubscribe_results['topics']:
      if qos is None:
        sys.exit("Server rejected resubscribe to topic: {}".format(topic))

def set_lightStatus(on):
  mqtt_connection.publish(
    topic="garden/lightStatus",
    payload=json.dumps({
      "on": on,
    }),
    qos=mqtt.QoS.AT_LEAST_ONCE
  )

def on_lightStatus_received(topic, payload):
  print("New lightStatus payload: {}".format(payload))
  lightStatus = json.loads(payload)['on']
  if lightStatus is True:
    light_relay.on()
  else:
    light_relay.off()

pump_last_on = datetime.now() + timedelta(minutes=-10)

def set_waterStatus(on):
  global pump_last_on
  if on:
    print("ON!")
    pump_timeout_engaged = (datetime.now() - pump_last_on).total_seconds() < 10 * 60
    if pump_timeout_engaged:
      print("Pump timeout engaged; ignoring request.")
      return
    
    pump_last_on = datetime.now()

  mqtt_connection.publish(
    topic="garden/lightStatus",
    payload=json.dumps({
      "on": on,
    }),
    qos=mqtt.QoS.AT_LEAST_ONCE
  )

def on_waterStatus_received(topic, payload):
  print("New waterStatus payload: {}".format(payload))
  waterStatus = json.loads(payload)['on']
  if waterStatus is True:
    pump_relay.on()
  else:
    pump_relay.off()

if __name__ == '__main__':
  # Spin up networking resources
  event_loop_group = io.EventLoopGroup(1)
  host_resolver = io.DefaultHostResolver(event_loop_group)
  client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

  mqtt_connection = mqtt_connection_builder.mtls_from_path(
    endpoint=args.endpoint,
    cert_filepath=args.cert,
    pri_key_filepath=args.key,
    client_bootstrap=client_bootstrap,
    ca_filepath=args.root_ca,
    on_connection_interrupted=on_connection_interrupted,
    on_connection_resumed=on_connection_resumed,
    client_id=args.client_id,
    clean_session=False,
    keep_alive_secs=6)

  print("Connecting to {} with client ID '{}'...".format(
    args.endpoint, args.client_id))
  connect_future = mqtt_connection.connect()
  connect_future.result()
  print("Connected!")

  # Subscribe
  print("Subscribing to topic garden/lightStatus...")
  subscribe_future, packet_id = mqtt_connection.subscribe(
    topic="garden/lightStatus",
    qos=mqtt.QoS.AT_LEAST_ONCE,
    callback=on_lightStatus_received)

  subscribe_result = subscribe_future.result()
  print("Subscribed with {}".format(str(subscribe_result['qos'])))

  print("Subscribing to topic garden/waterStatus...")
  subscribe_future, packet_id = mqtt_connection.subscribe(
    topic="garden/waterStatus",
    qos=mqtt.QoS.AT_LEAST_ONCE,
    callback=on_waterStatus_received)

  subscribe_result = subscribe_future.result()
  print("Subscribed with {}".format(str(subscribe_result['qos'])))

  refresh_interval = 10
  time_fetch_interval = 12 * 60 # Approx. minutes to refetch sunrise/sunset

  while True:
    # Initialize Astral time
    city = LocationInfo(
      args.city,
      args.region,
      args.timezone,
      args.lat,
      args.long
    )
    s = sun(observer=city.observer, tzinfo=args.timezone)
    print(str(city))
    print(str(s))
    for i in range(time_fetch_interval):
      temperature = soil.get_temp()
      capacitance = soil.moisture_read()
      print("Temp: " + str(temperature) + " Capacitance: " + str(capacitance))

      print("Publishing to topic garden/sensorData...")
      mqtt_connection.publish(
        topic="garden/sensorData",
        payload=json.dumps({
          "temperature": temperature,
          "capacitance": capacitance
        }),
        qos=mqtt.QoS.AT_LEAST_ONCE
      )

      if s['sunrise'] < datetime.now(tz=s['sunrise'].tzinfo) < s['sunset']:
        set_lightStatus(True)
      else:
        set_lightStatus(False)
      
      if capacitance < 400:
        set_waterStatus(True)
      else:
        set_waterStatus(False)

      time.sleep(refresh_interval)

  # Disconnect
  print("Disconnecting...")
  disconnect_future = mqtt_connection.disconnect()
  disconnect_future.result()
  print("Disconnected!")
