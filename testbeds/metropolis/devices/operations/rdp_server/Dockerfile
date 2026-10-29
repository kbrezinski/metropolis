FROM debian:trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 iproute2 hostname xrdp xorgxrdp openbox xauth xterm openssl \
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
