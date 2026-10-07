#!/usr/bin/env python3
"""Synthetic Mirai command-and-control console.

Mirrors the Gotham ``iotsim-mirai-cnc`` node: a controller that maintains a
registry of checked-in bots and lets an operator list them and dispatch
commands. This is a lab simulation of the control-plane choreography, not
Mirai. It never executes delivered commands, and no real bot binary exists in
this repository.

Operators drive it exactly like the Gotham console:

    bots                     list checked-in bots
    use <bot-id>             select a bot
    run <command>            record a command for the selected bot
    back                     clear the selection
    exit                     close the session
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field

LOG = logging.getLogger("mirai.cnc")

# Bound the registry so a runaway scanner cannot exhaust memory.
MAX_BOTS = 512
# Bound a single control line so a stuck peer cannot exhaust memory.
MAX_LINE = 512


@dataclass
class Bot:
    """One checked-in bot as the CNC sees it."""

    bot_id: str
    address: str
    arch: str
    reported_at: float
    commands: list[str] = field(default_factory=list)


class Registry:
    """Thread-safe registry of checked-in bots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bots: dict[str, Bot] = {}

    def check_in(self, address: str, arch: str) -> Bot:
        with self._lock:
            bot = self._bots.get(address)
            if bot is None:
                if len(self._bots) >= MAX_BOTS:
                    raise ValueError("Bot registry is full")
                bot = Bot(
                    bot_id=f"bot-{len(self._bots) + 1:04d}",
                    address=address,
                    arch=arch,
                    reported_at=time.time(),
                )
                self._bots[address] = bot
                LOG.info("bot %s checked in from %s (%s)", bot.bot_id, address, arch)
            else:
                bot.reported_at = time.time()
                bot.arch = arch
            return bot

    def get(self, bot_id: str) -> Bot | None:
        with self._lock:
            return next(
                (bot for bot in self._bots.values() if bot.bot_id == bot_id), None
            )

    def as_rows(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "bot_id": bot.bot_id,
                    "address": bot.address,
                    "arch": bot.arch,
                    "commands": len(bot.commands),
                }
                for bot in sorted(self._bots.values(), key=lambda item: item.bot_id)
            ]


def send_line(stream, text: str) -> None:
    stream.write((text + "\r\n").encode("utf-8"))
    stream.flush()


def report_listener(
    registry: Registry, host: str, port: int, stop: threading.Event
) -> None:
    """Accept bot check-ins and command polls on the scan-listener channel."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(64)
        server.settimeout(0.5)
        LOG.info("report channel listening on %s:%d", host, port)
        while not stop.is_set():
            try:
                connection, peer = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=handle_bot, args=(registry, connection, peer), daemon=True
            ).start()


def handle_bot(registry: Registry, connection: socket.socket, peer) -> None:
    """Serve one bot session: a check-in line, then command polls."""
    for _ in range(4):
        line = read_line(connection)
        if not line:
            return
        command = line.split(" ", 2)
        if command[0] == "CHECKIN" and len(command) >= 2:
            try:
                bot = registry.check_in(
                    command[1], command[2] if len(command) > 2 else "unknown"
                )
            except ValueError as exc:
                send_line(connection.makefile("wb"), f"REJECT {exc}")
                return
            send_line(connection.makefile("wb"), f"ACK {bot.bot_id}")
        elif command[0] == "CLEAR":
            return
    connection.close()


def read_line(connection: socket.socket) -> str:
    try:
        data = connection.recv(MAX_LINE)
    except OSError:
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def handle_operator(registry: Registry, connection: socket.socket) -> None:
    """Serve one operator console session."""
    stream = connection.makefile("wb")
    send_line(stream, "metropolis-mirai-cnc (synthetic lab console)")
    selection: Bot | None = None
    while True:
        line = read_line(connection)
        if not line:
            return
        parts = line.split(" ", 1)
        verb = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        if verb == "bots":
            for row in registry.as_rows():
                send_line(stream, json.dumps(row))
            send_line(stream, "END")
        elif verb == "use":
            selection = registry.get(argument)
            send_line(
                stream,
                f"using {selection.bot_id}" if selection else f"unknown bot {argument}",
            )
        elif verb == "run":
            if selection is None:
                send_line(stream, "select a bot first")
            elif not argument:
                send_line(stream, "run needs a command")
            else:
                # Recorded only; this lab never executes delivered commands.
                selection.commands.append(argument)
                send_line(stream, f"queued for {selection.bot_id}: {argument}")
        elif verb == "back":
            selection = None
            send_line(stream, "selection cleared")
        elif verb in {"exit", "quit"}:
            send_line(stream, "bye")
            return
        else:
            send_line(stream, "unknown command")


def operator_console(
    registry: Registry, host: str, port: int, stop: threading.Event
) -> None:
    """Accept operator sessions on the control channel."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(16)
        server.settimeout(0.5)
        LOG.info("operator console listening on %s:%d", host, port)
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=handle_operator, args=(registry, connection), daemon=True
            ).start()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.getenv("MIRAI_CNC_BIND", "0.0.0.0")
    control_port = int(os.getenv("MIRAI_CNC_PORT", "23"))
    report_port = int(os.getenv("MIRAI_REPORT_PORT", "48101"))
    stop = threading.Event()
    registry = Registry()
    threading.Thread(
        target=report_listener, args=(registry, host, report_port, stop), daemon=True
    ).start()
    try:
        operator_console(registry, host, control_port, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
