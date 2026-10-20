FROM debian:12-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        openvswitch-switch python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY testbeds/metropolis/devices/infrastructure/openvswitch/switch.py /opt/metropolis/switch.py

CMD ["python3", "-u", "/opt/metropolis/switch.py", "serve"]
