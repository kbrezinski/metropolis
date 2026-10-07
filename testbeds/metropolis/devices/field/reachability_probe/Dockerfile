FROM alpine:3.21

# A minimal reachable host. It runs no service and generates no traffic of its
# own; it exists to be pinged, connected to, or routed through, so every zone
# has a positive control before real devices are wired in. Gotham has an
# equivalent node (iotsim-debug-client) in both of its topologies.
#
# The shared entrypoint still applies NODE_HOSTNAME, NODE_IP, and NODE_GATEWAY
# before the idle command starts.
RUN apk add --no-cache iproute2 \
    && mkdir -p /opt/metropolis

COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["sleep", "infinity"]
