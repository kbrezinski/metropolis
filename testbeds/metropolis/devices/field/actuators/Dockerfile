FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ACTUATOR_ROLE=pump_drive \
    EIP_HOST=0.0.0.0 \
    EIP_PORT=44818

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/*
COPY testbeds/metropolis/devices/field/actuators/actuator.py /opt/metropolis/actuator.py
COPY testbeds/metropolis/devices/runtime/ethernet_ip.py /opt/metropolis/ethernet_ip.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

EXPOSE 44818/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/actuator.py"]
