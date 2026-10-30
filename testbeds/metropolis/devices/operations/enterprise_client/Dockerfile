FROM python:3.12-slim

ARG TELEMETRY_CERT_DIR=certs/telemetry
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TELEMETRY_INTERVAL=10
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/*
COPY testbeds/metropolis/devices/operations/enterprise_client/consumer.py /opt/metropolis/consumer.py
COPY testbeds/metropolis/devices/runtime/telemetry_http.py /opt/metropolis/telemetry_http.py
COPY ${TELEMETRY_CERT_DIR}/ca.crt /certs/ca.crt
COPY ${TELEMETRY_CERT_DIR}/reader.crt /certs/client.crt
COPY ${TELEMETRY_CERT_DIR}/reader.key /certs/client.key
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint && chmod 600 /certs/client.key
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/consumer.py"]
