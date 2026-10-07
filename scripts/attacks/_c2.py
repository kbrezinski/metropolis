"""Line-oriented console client for the synthetic C2 lab nodes.

Both synthetic consoles speak the same shape: UTF-8 lines with CRLF
terminators, the operator sends a verb, and the node replies with one or more
lines terminated by a sentinel such as ``END``. This module keeps that
single-line transport in one place so the Mirai and Merlin drivers share it.
"""

from __future__ import annotations

import socket

MAX_LINE = 8192
DEFAULT_TIMEOUT = 5.0


class ConsoleError(RuntimeError):
    """The console closed, timed out, or answered outside the protocol."""


class Console:
    """A single control-channel session against a synthetic console."""

    def __init__(self, host: str, port: int, timeout: float = DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket: socket.socket | None = None
        self._buffer = b""

    def __enter__(self) -> Console:
        try:
            self.socket = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
        except OSError as exc:
            raise ConsoleError(
                f"Could not reach {self.host}:{self.port}: {exc}"
            ) from exc
        self.socket.settimeout(self.timeout)
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def read_line(self) -> str:
        """Read one CRLF or LF terminated line."""
        while b"\n" not in self._buffer:
            if self.socket is None:
                raise ConsoleError("Console is not connected")
            try:
                chunk = self.socket.recv(4096)
            except socket.timeout as exc:
                raise ConsoleError("Timed out waiting for a console line") from exc
            except OSError as exc:
                raise ConsoleError(f"Console connection failed: {exc}") from exc
            if not chunk:
                raise ConsoleError("Console closed the connection")
            self._buffer += chunk
            if len(self._buffer) > MAX_LINE:
                raise ConsoleError("Console line exceeds the protocol limit")
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8", errors="replace").rstrip("\r")

    def send(self, text: str) -> None:
        if "\n" in text or "\r" in text:
            raise ValueError("Console commands must be single-line text")
        if self.socket is None:
            raise ConsoleError("Console is not connected")
        try:
            self.socket.sendall(text.encode("utf-8") + b"\r\n")
        except OSError as exc:
            raise ConsoleError(f"Could not send to the console: {exc}") from exc

    def command(self, text: str, sentinel: str = "END") -> list[str]:
        """Send a verb and collect lines until ``sentinel`` or the first reply."""
        self.send(text)
        lines: list[str] = []
        while True:
            line = self.read_line()
            if line == sentinel:
                return lines
            lines.append(line)
            if sentinel == "":
                return lines
