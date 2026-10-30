FROM python:3.12-slim

ARG TELEMETRY_CERT_DIR=certs/telemetry
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/*
COPY testbeds/metropolis/devices/services/telemetry_relay/relay.py /opt/metropolis/relay.py
COPY ${TELEMETRY_CERT_DIR}/ca.crt /certs/ca.crt
COPY ${TELEMETRY_CERT_DIR}/server.crt /certs/server.crt
COPY ${TELEMETRY_CERT_DIR}/server.key /certs/server.key
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint && chmod 600 /certs/server.key
EXPOSE 443/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/relay.py"]
