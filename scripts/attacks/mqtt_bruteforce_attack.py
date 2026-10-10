"""Bounded MQTT credential attempts against an inventoried lab broker."""

from __future__ import annotations

import os
import time
from pathlib import Path

from _common import (
    Run,
    add_tls_arguments,
    cli,
    endpoint,
    parser,
    passwords,
    positive,
    tls_from_args,
)
from _mqtt import BrokerConnection


def main():
    options = parser(__doc__, "MET-MQTT-BROKER-01")
    options.add_argument("--username")
    options.add_argument("--wordlist", type=Path)
    options.add_argument("--max-attempts", type=int, default=20)
    options.add_argument("--interval", type=positive, default=1)
    add_tls_arguments(options)
    args = options.parse_args()
    device, host, port = endpoint(args, "mqtt", "MQTT", 1883)
    env = device.get("environment", {})
    username = (
        args.username
        or os.getenv("METROPOLIS_MQTT_USERNAME")
        or env["MQTT_OPERATOR_USERNAME"]
    )
    candidates = passwords(
        args.wordlist,
        os.getenv("METROPOLIS_MQTT_PASSWORD") or env["MQTT_OPERATOR_PASSWORD"],
        args.max_attempts,
    )
    with Run(
        args,
        "mqtt_credentials",
        host=host,
        port=port,
        username=username,
        attempts=len(candidates),
    ) as run:
        if args.dry_run:
            return 0
        for index, candidate in enumerate(candidates):
            with BrokerConnection(
                host, port, username, candidate, args.timeout, tls_from_args(args)
            ) as connection:
                run.emit(
                    "attempt",
                    index=index + 1,
                    status=connection.status,
                    reason=connection.reason,
                )
                if connection.status == "accepted":
                    return 0
                if connection.status != "rejected":
                    return 2
            if index + 1 < len(candidates):
                time.sleep(args.interval)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli(main))
