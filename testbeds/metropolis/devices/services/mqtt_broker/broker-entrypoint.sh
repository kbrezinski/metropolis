#!/bin/sh
set -eu

# These defaults are synthetic credentials for an isolated research lab only.
device_user="${MQTT_DEVICE_USERNAME:-lab_device}"
device_password="${MQTT_DEVICE_PASSWORD:-LabOnly_Device_2026}"
operator_user="${MQTT_OPERATOR_USERNAME:-lab_operator}"
operator_password="${MQTT_OPERATOR_PASSWORD:-LabOnly_MQTT_2026}"
password_file="/mosquitto/data/passwordfile"
newline='
'

case "$device_user$operator_user" in
    *:*|*"$newline"*) echo "MQTT usernames may not contain ':' or newlines" >&2; exit 1 ;;
esac
case "$device_password$operator_password" in
    *"$newline"*) echo "MQTT passwords may not contain newlines" >&2; exit 1 ;;
esac

umask 077
mosquitto_passwd -b -c "$password_file" "$device_user" "$device_password"
mosquitto_passwd -b "$password_file" "$operator_user" "$operator_password"
chmod 600 "$password_file"

# An encrypted listener is added when TLS is switched on. The certificate and
# key must be mounted in, because a broker holding its own private key inside an
# image would be a worse idea than passing it in.
tls_enabled="$(printf '%s' "${MQTT_TLS:-false}" | tr '[:upper:]' '[:lower:]')"
if [ "$tls_enabled" = "true" ] || [ "$tls_enabled" = "1" ] || [ "$tls_enabled" = "yes" ]; then
    certfile="${TLS_CERT_FILE:-/mosquitto/certs/server.crt}"
    keyfile="${TLS_KEY_FILE:-/mosquitto/certs/server.key}"
    if [ ! -f "$certfile" ]; then
        echo "MQTT_TLS is enabled but the certificate $certfile is missing" >&2
        echo "Generate one with scripts/gen_lab_certificates.py and mount it in." >&2
        exit 1
    fi
    if [ ! -f "$keyfile" ]; then
        echo "MQTT_TLS is enabled but the key $keyfile is missing" >&2
        exit 1
    fi
    # Mosquitto refuses to start as root with a world-readable key, and the
    # container user is not the file's owner, so relax the key to owner-read.
    chmod 600 "$keyfile" 2>/dev/null || true

    cat >> /mosquitto/config/mosquitto.conf <<EOF

# Encrypted listener, added by the entrypoint.
listener ${MQTT_TLS_PORT:-8883} 0.0.0.0
cafile ${TLS_CA_FILE:-/mosquitto/certs/ca.crt}
certfile $certfile
keyfile $keyfile
tls_version tlsv1.2
EOF
    echo "MQTT TLS listener added on port ${MQTT_TLS_PORT:-8883}"
fi

exec /usr/local/bin/metropolis-entrypoint "$@"
