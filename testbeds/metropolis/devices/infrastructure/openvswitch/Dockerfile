FROM debian:12-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        openvswitch-switch python3 ca-certificates iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY testbeds/metropolis/devices/infrastructure/openvswitch/switch.py /opt/metropolis/switch.py
COPY testbeds/metropolis/devices/infrastructure/openvswitch/link_profile.py /opt/metropolis/link_profile.py

CMD ["python3", "-u", "/opt/metropolis/switch.py", "serve"]
