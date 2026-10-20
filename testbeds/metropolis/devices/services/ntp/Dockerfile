FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends chrony iproute2 hostname python3-minimal \
    && rm -rf /var/lib/apt/lists/*

COPY testbeds/metropolis/devices/services/ntp/chrony.conf /etc/metropolis-chrony.conf
COPY testbeds/metropolis/devices/services/ntp/syslog_receiver.py /opt/metropolis/syslog_receiver.py
COPY testbeds/metropolis/devices/services/ntp/start_services.py /opt/metropolis/start_services.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

EXPOSE 123/udp 514/udp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python3", "-u", "/opt/metropolis/start_services.py"]
