#!/bin/sh
set -eu

# GNS3 connects Docker NICs as virtual machine interfaces. Configure the
# selected interface before starting the image's application process.
node_hostname="${NODE_HOSTNAME:-}"
if [ -n "$node_hostname" ]; then
    if ! command -v hostname >/dev/null 2>&1; then
        echo "hostname command is required to set NODE_HOSTNAME" >&2
        exit 1
    fi
    hostname "$node_hostname"
fi

node_ip="${NODE_IP:-}"
node_gateway="${NODE_GATEWAY:-}"
node_interface="${NODE_INTERFACE:-eth0}"

if [ -n "$node_gateway" ] && [ -z "$node_ip" ]; then
    echo "NODE_GATEWAY requires NODE_IP to be set" >&2
    exit 1
fi

if [ -n "$node_ip" ]; then
    if ! command -v ip >/dev/null 2>&1; then
        echo "iproute2 is required to configure NODE_IP" >&2
        exit 1
    fi

    attempts=0
    until ip link show dev "$node_interface" >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 30 ]; then
            echo "Interface $node_interface did not appear; connect its GNS3 link and retry" >&2
            exit 1
        fi
        sleep 1
    done

    ip link set dev "$node_interface" up
    ip -4 address replace "$node_ip" dev "$node_interface"
    if [ -n "$node_gateway" ]; then
        ip -4 route replace default via "$node_gateway" dev "$node_interface"
    fi
fi

exec "$@"
