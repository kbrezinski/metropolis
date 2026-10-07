"""Small Telnet login client with incremental option negotiation."""

from __future__ import annotations

import socket
import time


class Rejected(Exception):
    pass


class Session:
    def __init__(self, host: str, port: int, timeout: float):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.timeout = timeout
        self.pending = bytearray()
        self.text = bytearray()

    def close(self):
        self.socket.close()

    def feed(self, data: bytes):
        self.pending.extend(data)
        index = 0
        while index < len(self.pending):
            value = self.pending[index]
            if value != 255:
                self.text.append(value)
                index += 1
                continue
            if index + 1 >= len(self.pending):
                break
            command = self.pending[index + 1]
            if command == 255:
                self.text.append(255)
                index += 2
            elif command in (251, 252, 253, 254):
                if index + 2 >= len(self.pending):
                    break
                option = self.pending[index + 2]
                if command == 251:  # WILL: accept echo and suppress-go-ahead.
                    self.socket.sendall(
                        bytes([255, 253 if option in (1, 3) else 254, option])
                    )
                elif command == 253:  # DO: only offer suppress-go-ahead.
                    self.socket.sendall(
                        bytes([255, 251 if option == 3 else 252, option])
                    )
                index += 3
            elif command == 250:  # Skip subnegotiation through IAC SE.
                end = self.pending.find(b"\xff\xf0", index + 2)
                if end == -1:
                    break
                index = end + 2
            else:
                index += 2
        del self.pending[:index]
        if len(self.pending) + len(self.text) > 65536:
            raise RuntimeError("Telnet response exceeds 64 KiB")

    def expect(self, markers: list[bytes]) -> bytes:
        deadline = time.monotonic() + self.timeout
        while True:
            lowered = bytes(self.text).lower()
            if b"login incorrect" in lowered or b"authentication failed" in lowered:
                raise Rejected("Login rejected")
            if any(marker.lower() in lowered for marker in markers):
                result = bytes(self.text)
                self.text.clear()
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Telnet prompt timed out")
            self.socket.settimeout(remaining)
            data = self.socket.recv(4096)
            if not data:
                raise ConnectionError("Telnet connection closed")
            self.feed(data)

    def line(self, text: str):
        if any(character in text for character in "\r\n\x00"):
            raise ValueError("Telnet login input must be single-line text")
        data = text.encode().replace(b"\xff", b"\xff\xff")
        self.socket.sendall(data + b"\r\n")

    def login(self, username: str, password: str):
        self.expect([b"login:", b"username:"])
        self.line(username)
        self.expect([b"password:"])
        self.line(password)
        reply = self.expect([b"# ", b"$ ", b"login:"])
        if b"login:" in reply.lower():
            raise Rejected("Login returned to username prompt")

    def prove_command(self, token: str, write_marker: bool) -> bool:
        # The full token is absent from the echoed command, avoiding a false
        # success when a Telnet server only echoes input without executing it.
        middle = len(token) // 2
        command = f"printf '%s%s\\n' '{token[:middle]}' '{token[middle:]}'"
        if write_marker:
            command = (
                "printf 'metropolis-login-test\\n' > /tmp/metropolis-login-test && "
                + command
            )
        self.line(command)
        reply = self.expect([token.encode()])
        return token.encode() in [line.strip() for line in reply.splitlines()]
