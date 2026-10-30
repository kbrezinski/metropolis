FROM debian:trixie-slim AS rdp-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 iproute2 hostname passwd xrdp xorgxrdp openbox xauth xterm openssl \
    && usermod -a -G ssl-cert xrdp \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/metropolis

COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
COPY testbeds/metropolis/devices/operations/rdp_server/start_rdp.py /opt/metropolis/start_rdp.py
COPY testbeds/metropolis/devices/operations/rdp_server/rdp_udp.py /opt/metropolis/rdp_udp.py
RUN chmod +x /usr/local/bin/metropolis-entrypoint

ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python3", "/opt/metropolis/start_rdp.py"]
EXPOSE 3389/tcp 3389/udp

FROM rdp-base AS rdp-relay
ARG TELEMETRY_CERT_DIR=certs/telemetry
ENV TELEMETRY_ENABLED=true
COPY testbeds/metropolis/devices/services/telemetry_relay/relay.py /opt/metropolis/relay.py
COPY ${TELEMETRY_CERT_DIR}/ca.crt /certs/ca.crt
COPY ${TELEMETRY_CERT_DIR}/server.crt /certs/server.crt
COPY ${TELEMETRY_CERT_DIR}/server.key /certs/server.key
RUN chmod 600 /certs/server.key
EXPOSE 443/tcp

# The default build has no relay credentials, including in its image layers.
FROM rdp-base AS rdp-server
