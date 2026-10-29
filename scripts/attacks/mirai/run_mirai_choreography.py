#!/usr/bin/env python3
# Adapted from Gotham: https://github.com/xsaga/gotham-iot-testbed/blob/master/src/run_mirai.py
"""Drive the declared synthetic Mirai bot through its C&C node.

This is a helper for ``mirai_scenario``. The invoking host sends operator
commands; the bot node makes the probe, report, and inert loader request.
"""

from __future__ import annotations

import json
import time

from _c2 import Console


def _bots(console: Console) -> list[dict]:
    rows = []
    for line in console.command("bots"):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _status(console: Console, bot_id: str) -> dict:
    reply = console.command(f"status {bot_id}", sentinel="")
    if not reply:
        raise RuntimeError("C&C did not answer the bot status request")
    try:
        status = json.loads(reply[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("C&C returned malformed bot status") from exc
    if status.get("bot_id") != bot_id:
        raise RuntimeError("C&C returned status for the wrong bot")
    return status


def _until(check, seconds: float, interval: float):
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value is not None:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def _queue(console: Console, bot_id: str, command: str) -> str:
    selected = console.command(f"use {bot_id}", sentinel="")
    if not selected or selected[0] != f"using {bot_id}":
        raise RuntimeError(f"C&C did not select bot {bot_id}")
    reply = console.command(f"run {command}", sentinel="")
    prefix = f"queued for {bot_id}: "
    if not reply or not reply[0].startswith(prefix):
        raise RuntimeError("C&C did not queue the bot task")
    return reply[0].removeprefix(prefix)


def _result(console: Console, bot_id: str, task_id: str) -> dict | None:
    status = _status(console, bot_id)
    return next(
        (item for item in status["results"] if item.get("task_id") == task_id), None
    )


def play_choreography(
    run,
    *,
    cnc_host: str,
    cnc_port: int,
    bot_node: str,
    bot_host: str,
    listener_host: str,
    listener_port: int,
    loader_host: str,
    loader_port: int,
    verified_targets: list[tuple[str, int]],
    delivery: str,
    interval: float,
    wait_seconds: float,
    command: str,
    payload_path: str,
    timeout: float,
) -> int:
    """Queue bounded bot work and require the bot's actual result reports."""
    with Console(cnc_host, cnc_port, timeout) as console:
        run.emit("cnc_banner", source="invoking_host", banner=console.read_line())

        def find_bot():
            return next(
                (
                    row
                    for row in _bots(console)
                    if row.get("address") == bot_host
                    and row.get("peer_address") == bot_host
                ),
                None,
            )

        row = _until(find_bot, wait_seconds, interval)
        if row is None:
            raise RuntimeError(
                f"C&C has no check-in from declared bot {bot_node} at {bot_host}"
            )
        bot_id = row["bot_id"]
        run.emit(
            "bot_checkin_observed",
            source=bot_node,
            bot_id=bot_id,
            address=bot_host,
            peer_address=row["peer_address"],
        )

        dispatched = 0
        for target, target_port in verified_targets:
            action = json.dumps(
                {
                    "op": "probe",
                    "target": target,
                    "target_port": target_port,
                    "listener_host": listener_host,
                    "listener_port": listener_port,
                    "loader_host": loader_host,
                    "loader_port": loader_port,
                    "delivery": delivery,
                    "payload_path": payload_path,
                },
                separators=(",", ":"),
            )
            task_id = _queue(console, bot_id, action)
            run.emit(
                "bot_task_queued",
                source="invoking_host",
                bot_id=bot_id,
                task_id=task_id,
                target=f"{target}:{target_port}",
                delivery=delivery,
            )
            result = _until(
                lambda: _result(console, bot_id, task_id), wait_seconds, interval
            )
            if result is None:
                raise RuntimeError(f"Bot did not acknowledge task {task_id}")
            run.emit("bot_task_result", source=bot_node, bot_id=bot_id, **result)
            if result.get("outcome") != "complete":
                raise RuntimeError(
                    f"Bot task {task_id} failed: {result.get('outcome')}"
                )
            dispatched += 1

        if command:
            inert = json.dumps({"op": "record", "text": command}, separators=(",", ":"))
            task_id = _queue(console, bot_id, inert)
            result = _until(
                lambda: _result(console, bot_id, task_id), wait_seconds, interval
            )
            if result is None or result.get("outcome") != "recorded":
                raise RuntimeError("Bot did not acknowledge the inert command")
            run.emit(
                "inert_command_acknowledged",
                source=bot_node,
                bot_id=bot_id,
                task_id=task_id,
            )
    return dispatched
