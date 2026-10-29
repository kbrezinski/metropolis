"""Routine Modbus operator client with DNS, NTP, SNMP, and CoAP requests."""

from __future__ import annotations

import logging
import os
import secrets
import socket
import struct
import subprocess
import time
from ipaddress import IPv4Address

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("metropolis.control_client")


def lookup_plc(name: str, dns_server: str) -> str:
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [dns_server]
    resolver.timeout = 2
    resolver.lifetime = 3
    answer = resolver.resolve(name, "A", search=False)
    return str(answer[0])


def sample_ntp(server: str) -> float:
    """Return the remote transmit time; do not adjust the container clock."""
    packet = bytes([0x23]) + bytes(47)  # NTP v4 client request.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.sendto(packet, (server, 123))
        response, _ = sock.recvfrom(512)
    if len(response) < 48 or response[0] & 7 != 4:
        raise ValueError("Invalid NTP server response")
    seconds, fraction = struct.unpack_from("!II", response, 40)
    if seconds == 0:
        raise ValueError("NTP server has no transmit timestamp")
    return seconds - 2208988800 + fraction / 2**32


def snmp_targets(value: str) -> list[tuple[str, str]]:
    """Read manager targets from the selected dataset's device environment."""
    targets = []
    names = set()
    for item in filter(None, (part.strip() for part in value.split(","))):
        name, separator, address = item.partition("=")
        if not separator or not name or name in names:
            raise ValueError("Invalid SNMP target list")
        targets.append((name, str(IPv4Address(address.strip()))))
        names.add(name)
    return targets


def sample_snmp(host: str, community: str) -> str:
    """Read the standard sysUpTime OID without changing router state."""
    result = subprocess.run(
        [
            "snmpget",
            "-v2c",
            "-c",
            community,
            "-Oqv",
            "-t",
            "2",
            "-r",
            "0",
            host,
            "1.3.6.1.2.1.1.3.0",
        ],
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("SNMP query failed")
    return result.stdout.strip()


def sample_coap(host: str, port: int) -> tuple[str, int, int]:
    """Read the sensor's CoAP status resource without changing its state."""
    message_id = secrets.randbelow(65536)
    # Confirmable GET, no token, Uri-Path option 11 with the value "status".
    request = struct.pack("!BBH", 0x40, 1, message_id) + b"\xb6status"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.connect((host, port))
        sock.send(request)
        response = sock.recv(2048)
    if (
        len(response) < 4
        or response[0] != 0x60  # Version 1, ACK, no token.
        or struct.unpack_from("!H", response, 2)[0] != message_id
        or response[1] == 0
    ):
        raise ValueError("Invalid CoAP status response")
    code = f"{response[1] >> 5}.{response[1] & 31:02d}"
    return code, len(request), len(response)


def control_cycle(plc_host: str, port: int, unit_id: int, level: int, pump: bool):
    """Read the current process snapshot, then issue bounded routine writes."""
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient(plc_host, port=port, timeout=3, retries=0)
    try:
        if not client.connect():
            raise ConnectionError(f"Could not connect to PLC {plc_host}:{port}")
        read = client.read_holding_registers(address=0, count=4, slave=unit_id)
        if read.isError():
            raise RuntimeError(f"PLC read failed: {read}")
        level_write = client.write_register(address=0, value=level, slave=unit_id)
        if level_write.isError():
            raise RuntimeError(f"PLC level write failed: {level_write}")
        pump_write = client.write_coil(address=0, value=pump, slave=unit_id)
        if pump_write.isError():
            raise RuntimeError(f"PLC pump write failed: {pump_write}")
        confirm = client.read_holding_registers(address=0, count=1, slave=unit_id)
        coil = client.read_coils(address=0, count=1, slave=unit_id)
        if confirm.isError() or coil.isError():
            raise RuntimeError("PLC write confirmation failed")
        return read.registers[0], confirm.registers[0], bool(coil.bits[0])
    finally:
        client.close()


def main() -> None:
    dns_server = os.getenv("DNS_SERVER", "10.20.30.10")
    ntp_server = os.getenv("NTP_SERVER", "10.20.30.11")
    plc_name = os.getenv("PLC_DNS_NAME", "plc-intake.metropolis.test")
    interval = max(5, float(os.getenv("CONTROL_INTERVAL", "30")))
    port = int(os.getenv("MODBUS_PORT", "502"))
    unit_id = int(os.getenv("MODBUS_UNIT_ID", "1"))
    targets = snmp_targets(os.getenv("SNMP_TARGETS", ""))
    community = os.getenv("SNMP_COMMUNITY", "")
    coap_host = os.getenv("COAP_HOST", "")
    coap_port = int(os.getenv("COAP_PORT", "5683"))
    coap_target = os.getenv("COAP_TARGET_NODE", coap_host)
    source = os.getenv("NODE_HOSTNAME", socket.gethostname())
    cycle = 0
    while True:
        try:
            host = lookup_plc(plc_name, dns_server)
            try:
                timestamp = sample_ntp(ntp_server)
            except (OSError, ValueError) as exc:
                log.warning("NTP sample failed: %s", exc)
                timestamp = None
            # Stable, modest variation produces regular writes and reads.
            level = 650 + (cycle % 5) * 2
            pump = cycle % 8 < 6
            before, after, actual_pump = control_cycle(host, port, unit_id, level, pump)
            log.info(
                "cycle=%s ntp=%s plc=%s level=%s->%s pump=%s",
                cycle,
                timestamp,
                host,
                before,
                after,
                actual_pump,
            )
            cycle += 1
        except Exception as exc:  # Keep baseline traffic alive during node outages.
            log.warning("Routine control cycle failed: %s", exc)
        for name, target in targets:
            try:
                if not community:
                    raise RuntimeError("SNMP manager community is unset")
                uptime = sample_snmp(target, community)
                log.info("SNMP poll succeeded: router=%s uptime=%s", name, uptime)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                # Never include command arguments or community in collected logs.
                log.warning("SNMP poll failed: router=%s", name)
        if coap_host:
            try:
                code, request_bytes, response_bytes = sample_coap(coap_host, coap_port)
                log.info(
                    "CoAP poll: source=%s target=%s resource=/status code=%s request_bytes=%s response_bytes=%s",
                    source,
                    coap_target,
                    code,
                    request_bytes,
                    response_bytes,
                )
            except (OSError, ValueError):
                log.warning(
                    "CoAP poll failed: source=%s target=%s resource=/status",
                    source,
                    coap_target,
                )
        time.sleep(interval)


if __name__ == "__main__":
    main()
