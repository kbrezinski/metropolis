#!/bin/sh
set -eu

# Deliberately isolated legacy node. The public credential belongs only to
# the simulated lab and is supplied by the GNS3 node environment.
legacy_password="${LEGACY_ROOT_PASSWORD:-}"
if [ -z "$legacy_password" ]; then
    echo '{"event":"telnet_start_failed","level":"ERROR","message":"LEGACY_ROOT_PASSWORD is required"}' >&2
    exit 1
fi
case "$legacy_password" in
    *:*) echo '{"event":"telnet_start_failed","level":"ERROR","message":"invalid legacy password format"}' >&2; exit 1 ;;
esac
printf 'root:%s\n' "$legacy_password" | chpasswd
unset legacy_password LEGACY_ROOT_PASSWORD

# BusyBox Telnet provides a login prompt and an actual Linux shell. Bind only
# inside the GNS3 node; the surrounding lab must be isolated by its routing.
echo '{"event":"telnet_starting","level":"INFO","message":"legacy gateway starting on TCP 23"}'
exec /bin/busybox-extras telnetd -F -p 23 -l /bin/login
