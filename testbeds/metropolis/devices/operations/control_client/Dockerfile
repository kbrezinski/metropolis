FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLC_DNS_NAME=plc-intake.metropolis.test \
    DNS_SERVER=10.20.30.10 \
    NTP_SERVER=10.20.30.11 \
    MODBUS_PORT=502 \
    MODBUS_UNIT_ID=1 \
    CONTROL_INTERVAL=30

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pymodbus==3.8.6 dnspython==2.7.0

COPY testbeds/metropolis/devices/operations/control_client/control_client.py /opt/metropolis/control_client.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/control_client.py"]
