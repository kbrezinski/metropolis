FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/metropolis

COPY testbeds/metropolis/devices/attack/mirai/loader/start_loader.py /opt/metropolis/start_loader.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

# Same model as the loader, pinned to the wget delivery role.
ENV LOADER_KIND=wget LOADER_PORT=80
EXPOSE 80/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python3", "/opt/metropolis/start_loader.py"]
