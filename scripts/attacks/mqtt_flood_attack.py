"""Bounded MQTT topic traffic and retained-message experiments."""

from __future__ import annotations

import json
import os

from _common import (
    Run,
    add_tls_arguments,
    cli,
    endpoint,
    limits,
    parser,
    positive,
    schedule,
    tls_from_args,
)
from _mqtt import BrokerConnection


def main():
    options = parser(__doc__, "MET-MQTT-BROKER-01")
    options.add_argument(
        "--mode", choices=["flood", "retain", "clear-retain"], default="flood"
    )
    options.add_argument(
        "--topic-base",
        default=os.getenv("METROPOLIS_MQTT_TOPIC", "metropolis/experiment/telemetry"),
    )
    options.add_argument("--username")
    options.add_argument("--password-env", default="METROPOLIS_MQTT_PASSWORD")
    options.add_argument("--rate", type=positive, default=10)
    options.add_argument("--duration", type=positive, default=10)
    options.add_argument("--max-messages", type=int, default=100)
    add_tls_arguments(options)
    args = options.parse_args()
    limits(args)
    if not args.topic_base or any(char in args.topic_base for char in "+#\x00"):
        raise ValueError("Use a non-empty concrete topic without wildcards")
    device, host, port = endpoint(args, "mqtt", "MQTT", 1883)
    env = device["environment"]
    username = (
        args.username
        or os.getenv("METROPOLIS_MQTT_USERNAME")
        or env["MQTT_OPERATOR_USERNAME"]
    )
    password = os.getenv(args.password_env) or env["MQTT_OPERATOR_PASSWORD"]
    with Run(
        args,
        "mqtt_traffic",
        host=host,
        port=port,
        mode=args.mode,
        topic=args.topic_base,
        rate=args.rate,
        duration=args.duration,
    ) as run:
        if args.dry_run:
            return 0
        confirmed = 0
        with BrokerConnection(
            host, port, username, password, args.timeout, tls_from_args(args)
        ) as connection:
            if connection.status != "accepted":
                raise ConnectionError(f"Broker connection: {connection.status}")
            if args.mode == "clear-retain":
                connection.publish(args.topic_base, b"", True, args.timeout)
                run.emit("summary", confirmed=1, cleared_topic=args.topic_base)
                return 0
            for index in schedule(args.rate, args.duration, args.max_messages):
                topic = (
                    f"{args.topic_base}/{index}"
                    if args.mode == "flood"
                    else args.topic_base
                )
                payload = json.dumps(
                    {
                        "device": "MET-SENSOR-INTAKE-01",
                        "sensor": "level",
                        "value": 65 + index % 10,
                    }
                )
                connection.publish(topic, payload, args.mode == "retain", args.timeout)
                confirmed += 1
        run.emit(
            "summary",
            broker_confirmed=confirmed,
            retained_topic=args.topic_base if args.mode == "retain" else None,
        )
        return 0 if confirmed else 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
