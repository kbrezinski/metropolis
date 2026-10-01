FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname openssh-server openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/ssh/ssh_host_* \
    && useradd --create-home --shell /bin/bash engineer \
    && pip install --no-cache-dir pymodbus==3.8.6 paho-mqtt==2.1.0 \
    && mkdir -p /opt/metropolis
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
COPY testbeds/metropolis/devices/operations/engineering_workstation/sshd_config /etc/ssh/sshd_config_metropolis
COPY testbeds/metropolis/devices/operations/engineering_workstation/start_ssh.py /opt/metropolis/start_ssh.py
RUN chmod +x /usr/local/bin/metropolis-entrypoint
WORKDIR /opt/metropolis
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/start_ssh.py"]
EXPOSE 22/tcp
