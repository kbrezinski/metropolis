FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEVICE_NAME=MET-SCADA-01 \
    MQTT_USERNAME=lab_device \
    MQTT_PASSWORD=LabOnly_Device_2026 \
    PLC_HOST=10.20.10.10 \
    MODBUS_PORT=502 \
    MODBUS_UNIT_ID=1 \
    MQTT_HOST=10.20.23.10 \
    MQTT_PORT=1883 \
    MQTT_TOPIC=metropolis/scada/intake/status \
    POLL_INTERVAL=5

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pymodbus==3.8.6 paho-mqtt==2.1.0
COPY testbeds/metropolis/devices/operations/scada/scada_collector.py /opt/metropolis/scada_collector.py
COPY testbeds/metropolis/devices/runtime/tls_config.py /opt/metropolis/tls_config.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/scada_collector.py"]
