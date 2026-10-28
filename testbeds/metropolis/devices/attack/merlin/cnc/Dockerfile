# Adapted from Gotham's Merlin C&C role: https://github.com/xsaga/gotham-iot-testbed/blob/master/Dockerfiles/malware/Merlin/Dockerfile
FROM python:3.12-slim

ARG MERLIN_CERT_DIR=certs/merlin

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/metropolis

COPY testbeds/metropolis/devices/attack/merlin/cnc/start_merlin_cnc.py /opt/metropolis/start_merlin_cnc.py
COPY ${MERLIN_CERT_DIR}/server.crt /opt/metropolis/tls/server.crt
COPY ${MERLIN_CERT_DIR}/server.key /opt/metropolis/tls/server.key
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint \
    && chmod 600 /opt/metropolis/tls/server.key

EXPOSE 23/tcp 8443/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python3", "/opt/metropolis/start_merlin_cnc.py"]
