FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEVICE_NAME=MET-HISTORIAN-01 \
    MQTT_USERNAME=lab_device \
    MQTT_PASSWORD=LabOnly_Device_2026 \
    MQTT_HOST=10.20.23.10 \
    MQTT_PORT=1883 \
    MQTT_TOPIC=metropolis/#

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir paho-mqtt==2.1.0
COPY testbeds/metropolis/devices/operations/historian/historian_collector.py /opt/metropolis/historian_collector.py
COPY testbeds/metropolis/devices/runtime/tls_config.py /opt/metropolis/tls_config.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/historian_collector.py"]
