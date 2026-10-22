FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODBUS_HOST=0.0.0.0 \
    MODBUS_PORT=502 \
    LEVEL_UPDATE_INTERVAL=1 \
    LEVEL_MIN_X10=400 \
    LEVEL_MAX_X10=700 \
    LEVEL_CYCLE_SECONDS=180

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pymodbus==3.8.6
COPY testbeds/metropolis/devices/field/level_instrument/level_instrument.py /opt/metropolis/level_instrument.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint

RUN chmod +x /usr/local/bin/metropolis-entrypoint

EXPOSE 502/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/level_instrument.py"]
