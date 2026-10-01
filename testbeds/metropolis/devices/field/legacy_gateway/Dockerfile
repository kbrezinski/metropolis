FROM alpine:3.21

RUN apk add --no-cache busybox-extras shadow iproute2 \
    && busybox-extras --list | grep -qx telnetd \
    && for n in $(seq 0 31); do echo "pts/$n" >> /etc/securetty; done \
    && mkdir -p /opt/metropolis

COPY testbeds/metropolis/devices/field/legacy_gateway/start_telnet.sh /opt/metropolis/start_telnet.sh
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint /opt/metropolis/start_telnet.sh

EXPOSE 23/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["/opt/metropolis/start_telnet.sh"]
