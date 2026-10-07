"""Nmap service scanning with explicit TCP and UDP lab destinations."""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
from pathlib import Path

from _common import Run, cli, inventory, ipv4, parser


def main():
    options = parser(__doc__, "all")
    options.add_argument(
        "--cidr", help="Optional private IPv4 range, maximum 256 addresses"
    )
    options.add_argument("--protocol", choices=["tcp", "udp", "both"], default="tcp")
    options.add_argument("--out", type=Path, help="Nmap XML result file")
    args = options.parse_args()
    if args.port is not None:
        raise ValueError("Scanner uses the documented port set; --port is unsupported")
    if sum(bool(item) for item in (args.host, args.cidr, args.node != "all")) > 1:
        raise ValueError("Select only one of --host, --cidr, or --node")
    if args.host:
        hosts = [ipv4(args.host)]
    elif args.cidr:
        subnet = ipaddress.IPv4Network(args.cidr, strict=True)
        if subnet.num_addresses > 256:
            raise ValueError("Scan ranges must contain at most 256 addresses")
        hosts = [ipv4(str(address)) for address in subnet.hosts()]
    else:
        devices = inventory(args.inventory)
        if args.node != "all":
            devices = [item for item in devices if item["name"] == args.node]
        hosts = sorted({ipv4(item["address"].split("/")[0]) for item in devices})
    if not 1 <= len(hosts) <= 256:
        raise ValueError("Select 1–256 inventoried or private lab hosts")
    command = ["nmap", "-Pn", "-sV", "--max-retries", "1", "--host-timeout", "15s"]
    if args.protocol in {"tcp", "both"}:
        command.append("-sT")
    if args.protocol in {"udp", "both"}:
        command.append("-sU")
    ports = []
    if args.protocol in {"tcp", "both"}:
        ports.append("T:22,23,53,502,1883,8080")
    if args.protocol in {"udp", "both"}:
        ports.append("U:53,123,5683")
    command.extend(["-p", ",".join(ports)])
    if args.out:
        command.extend(["-oX", str(args.out)])
    command.extend(hosts)
    with Run(args, "network_scan", command=command, protocol=args.protocol) as run:
        if args.dry_run:
            return 0
        if not shutil.which("nmap"):
            raise RuntimeError("Install Nmap on the experiment host")
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
        # Capture Nmap's text as an event, preserving JSONL stdout.
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=20 * len(hosts) + 30
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Nmap exceeded the run timeout") from exc
        run.emit(
            "result",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return 0 if result.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(cli(main))
