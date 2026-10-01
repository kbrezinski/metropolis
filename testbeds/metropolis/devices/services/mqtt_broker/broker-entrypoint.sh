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

exec /usr/local/bin/metropolis-entrypoint "$@"
