"""Bounded Modbus writes with readback against the lab PLC/RTU model."""

from __future__ import annotations

from _common import Run, cli, endpoint, parser, positive, schedule


def checked(response):
    if response.isError():
        raise RuntimeError(f"Modbus exception response: {response}")
    return response


def read_value(client, address, unit, coil):
    if coil:
        return bool(
            checked(client.read_coils(address=address, count=1, slave=unit)).bits[0]
        )
    return checked(
        client.read_holding_registers(address=address, count=1, slave=unit)
    ).registers[0]


def write_value(client, address, value, unit, coil):
    if coil:
        checked(client.write_coil(address=address, value=value, slave=unit))
    else:
        checked(client.write_register(address=address, value=value, slave=unit))


def main():
    options = parser(__doc__, "MET-PLC-INTAKE-01")
    options.add_argument("--unit-id", type=int, default=1)
    options.add_argument(
        "--action",
        required=True,
        choices=[
            "set-level",
            "set-flow",
            "set-quality",
            "force-pump",
            "release-pump",
            "overwrite-heartbeat",
            "freeze-heartbeat",
        ],
    )
    options.add_argument("--value", type=int, default=999)
    options.add_argument(
        "--restore", action="store_true", help="Restore the initial value on exit"
    )
    options.add_argument("--duration", type=positive, default=10)
    options.add_argument("--period", type=positive, default=1)
    options.add_argument("--max-messages", type=int, default=100)
    args = options.parse_args()
    _, host, port = endpoint(args, "modbus-tcp", "PLC", 502)
    if not 0 <= args.value <= 65535 or not 0 <= args.unit_id <= 255:
        raise ValueError("Value must be 0–65535 and unit ID 0–255")
    if (
        args.duration > 3600
        or args.period < 0.001
        or not 1 <= args.max_messages <= 100000
    ):
        raise ValueError(
            "Use duration <=3600, period >=0.001, and max-messages 1–100000"
        )
    heartbeat = args.action in {"overwrite-heartbeat", "freeze-heartbeat"}
    if heartbeat and args.restore:
        raise ValueError("Cannot restore an advancing heartbeat to its initial value")
    coil = args.action in {"force-pump", "release-pump"}
    address = {
        "set-level": 0,
        "set-flow": 1,
        "set-quality": 2,
        "overwrite-heartbeat": 3,
        "freeze-heartbeat": 3,
    }.get(args.action, 0)
    value = (args.action == "force-pump") if coil else (0 if heartbeat else args.value)
    with Run(
        args,
        "modbus_write",
        host=host,
        port=port,
        action=args.action,
        address=address,
        value=value,
        unit=args.unit_id,
        restore=args.restore,
    ) as run:
        if args.dry_run:
            return 0
        from pymodbus.client import ModbusTcpClient

        client = ModbusTcpClient(host, port=port, timeout=args.timeout, retries=0)
        original = None
        wrote = False
        try:
            if not client.connect():
                raise ConnectionError("PLC connection failed")
            original = read_value(client, address, args.unit_id, coil)
            run.emit("before", value=original)
            cycles = (
                schedule(1 / args.period, args.duration, args.max_messages)
                if heartbeat
                else [0]
            )
            confirmed = 0
            for index in cycles:
                write_value(client, address, value, args.unit_id, coil)
                wrote = True
                actual = read_value(client, address, args.unit_id, coil)
                run.emit(
                    "write",
                    index=index + 1,
                    requested=value,
                    observed=actual,
                    matched=actual == value,
                )
                confirmed += actual == value
            if heartbeat:
                run.emit(
                    "note",
                    evidence="Repeated heartbeat overwrites; the PLC continues its own timer",
                )
            return 0 if confirmed else 1
        finally:
            try:
                if args.restore and wrote:
                    write_value(client, address, original, args.unit_id, coil)
                    actual = read_value(client, address, args.unit_id, coil)
                    run.emit("restored", requested=original, observed=actual)
                    if actual != original:
                        raise RuntimeError(
                            "Original value was not restored at readback"
                        )
            finally:
                client.close()


if __name__ == "__main__":
    raise SystemExit(cli(main))
