FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEVICE_NAME=MET-SENSOR-INTAKE-01 \
    MQTT_USERNAME=lab_device \
    MQTT_PASSWORD=LabOnly_Device_2026 \
    MQTT_HOST=10.20.23.10 \
    MQTT_PORT=1883 \
    MQTT_TOPIC=metropolis/plant/intake/telemetry \
    PUBLISH_INTERVAL=5 \
    SENSOR_TYPE=level \
    SENSOR_VALUE=65.0 \
    COAP_ENABLED=true \
    COAP_PORT=5683

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir paho-mqtt==2.1.0
COPY testbeds/metropolis/devices/field/sensors/mqtt_sensor.py /opt/metropolis/mqtt_sensor.py
COPY testbeds/metropolis/devices/field/sensors/coap_sensor.py /opt/metropolis/coap_sensor.py
COPY testbeds/metropolis/devices/runtime/tls_config.py /opt/metropolis/tls_config.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/mqtt_sensor.py"]
EXPOSE 5683/udp
