# Adapted from Gotham's Merlin agent role: https://github.com/xsaga/gotham-iot-testbed/blob/master/src/run_merlin.py
FROM python:3.12-slim

ARG MERLIN_CERT_DIR=certs/merlin

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/metropolis

COPY testbeds/metropolis/devices/attack/merlin/agent/start_merlin_agent.py /opt/metropolis/start_merlin_agent.py
COPY ${MERLIN_CERT_DIR}/ca.crt /opt/metropolis/tls/ca.crt
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

# MERLIN_CNC_URL must point at the CNC node, for example
# https://10.99.10.20:8443 . Set it in the GNS3 node environment.
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python3", "/opt/metropolis/start_merlin_agent.py"]
