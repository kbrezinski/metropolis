FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends chrony iproute2 hostname \
    && rm -rf /var/lib/apt/lists/*

COPY testbeds/metropolis/devices/services/ntp/chrony.conf /etc/metropolis-chrony.conf
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

EXPOSE 123/udp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["chronyd", "-d", "-x", "-f", "/etc/metropolis-chrony.conf"]
