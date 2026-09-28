FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pymodbus==3.8.6 paho-mqtt==2.1.0 \
    && mkdir -p /opt/metropolis
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint
WORKDIR /opt/metropolis
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["/bin/sh", "-lc", "sleep infinity"]
