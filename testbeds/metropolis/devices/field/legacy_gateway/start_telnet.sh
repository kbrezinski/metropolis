#!/bin/sh
set -eu

# Deliberately isolated legacy node. The public credential belongs only to
# the simulated lab and is supplied by the GNS3 node environment.
legacy_password="${LEGACY_ROOT_PASSWORD:-}"
if [ -z "$legacy_password" ]; then
    echo "LEGACY_ROOT_PASSWORD is required" >&2
    exit 1
fi
case "$legacy_password" in
    *:*) echo "LEGACY_ROOT_PASSWORD may not contain ':'" >&2; exit 1 ;;
esac
printf 'root:%s\n' "$legacy_password" | chpasswd
unset legacy_password LEGACY_ROOT_PASSWORD

# BusyBox Telnet provides a login prompt and an actual Linux shell. Bind only
# inside the GNS3 node; the surrounding lab must be isolated by its routing.
exec /bin/busybox-extras telnetd -F -p 23 -l /bin/login
