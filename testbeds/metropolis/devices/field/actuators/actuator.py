"""Networked pump-drive or valve-controller simulator with CIP explicit messaging."""

from __future__ import annotations

import json
import os
import socketserver
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))

from ethernet_ip import (
    CLASS,
    REGISTER_SESSION,
    SEND_RR_DATA,
    cip_from_rr,
    receive_packet,
    rr_from_cip,
    send_packet,
)


class State:
    def __init__(self, role: str, faulted: bool = False) -> None:
        if role not in {"pump_drive", "valve_controller"}:
            raise ValueError(f"Unsupported actuator role: {role}")
        self.role = role
        self.faulted = faulted
        self.active = role == "valve_controller" and not faulted
        self.lock = threading.Lock()

    def handle(self, cip: bytes) -> bytes:
        service = cip[0] if cip else 0
        valid_path = len(cip) >= 8 and cip[1:8] == bytes(
            (3, 0x20, CLASS, 0x24, 1, 0x30, 1)
        )
        status = 0 if valid_path and service in (0x0E, 0x10) else 0x08
        if status == 0 and service == 0x10 and len(cip) != 9:
            status = 0x13
        with self.lock:
            if status == 0 and service == 0x10:
                changed = self.active != bool(cip[8])
                self.active = bool(cip[8]) and not self.faulted
                if changed:
                    print(
                        json.dumps(
                            {
                                "event": "actuator_state_changed",
                                "role": self.role,
                                "active": self.active,
                            }
                        ),
                        flush=True,
                    )
            active = self.active
        return bytes((service | 0x80, 0, status, 0, int(active)))


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        session = 0
        try:
            while True:
                command, incoming_session, payload = receive_packet(self.request)
                if command == REGISTER_SESSION and payload == struct.pack("<HH", 1, 0):
                    session = 1
                    send_packet(self.request, command, session, payload)
                elif (
                    command == SEND_RR_DATA and session and incoming_session == session
                ):
                    answer = self.server.state.handle(cip_from_rr(payload))
                    send_packet(self.request, command, session, rr_from_cip(answer))
                else:
                    break
        except (ConnectionError, OSError, ValueError):
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: State) -> None:
        super().__init__(address, Handler)
        self.state = state


def main() -> None:
    state = State(
        os.getenv("ACTUATOR_ROLE", "pump_drive"),
        os.getenv("ACTUATOR_FAULTED", "false").lower() == "true",
    )
    host = os.getenv("EIP_HOST", "0.0.0.0")
    port = int(os.getenv("EIP_PORT", "44818"))
    with Server((host, port), state) as server:
        print(
            json.dumps({"event": "actuator_started", "role": state.role, "port": port}),
            flush=True,
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
