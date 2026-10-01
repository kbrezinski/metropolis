FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends dnsmasq-base iproute2 hostname \
    && rm -rf /var/lib/apt/lists/*

COPY testbeds/metropolis/devices/services/dns/dnsmasq.conf /etc/metropolis-dnsmasq.conf
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

EXPOSE 53/udp 53/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["dnsmasq", "--no-daemon", "--conf-file=/etc/metropolis-dnsmasq.conf"]
