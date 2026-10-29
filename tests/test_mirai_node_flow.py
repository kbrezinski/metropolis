"""Loopback contracts for scenario-triggered synthetic Mirai node traffic."""

from __future__ import annotations

import importlib.util
import json
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIRAI = ROOT / "testbeds/metropolis/devices/attack/mirai"
sys.path.insert(0, str(ROOT / "scripts/attacks"))
sys.path.insert(0, str(ROOT / "scripts/attacks/mirai"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bot = load("mirai_node_bot", MIRAI / "bot/start_bot.py")
cnc = load("mirai_node_cnc", MIRAI / "cnc/start_cnc.py")
listener = load("mirai_node_listener", MIRAI / "scan_listener/start_listener.py")
loader = load("mirai_node_loader", MIRAI / "loader/start_loader.py")
choreography = load(
    "mirai_node_choreography", ROOT / "scripts/attacks/mirai/run_mirai_choreography.py"
)


@pytest.fixture
def tcp_servers():
    servers = []

    def start(handler):
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        server = Server(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server.server_address[1]

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cnc_delivers_task_to_checked_in_bot_and_records_ack(tcp_servers):
    registry = cnc.Registry()

    class BotHandler(socketserver.BaseRequestHandler):
        def handle(self):
            cnc.handle_bot(registry, self.request, self.client_address)

    class OperatorHandler(socketserver.BaseRequestHandler):
        def handle(self):
            cnc.handle_operator(registry, self.request)

    bot_port = tcp_servers(BotHandler)
    operator_port = tcp_servers(OperatorHandler)
    bot_id = bot.check_in("127.0.0.1", bot_port, "lab", 2)
    assert bot_id
    assert bot.poll("127.0.0.1", bot_port, bot_id, 2) is None

    from _c2 import Console

    with Console("127.0.0.1", operator_port, 2) as console:
        console.read_line()
        assert registry.as_rows()[0]["peer_address"] == "127.0.0.1"
        assert console.command(f"use {bot_id}", sentinel="") == [f"using {bot_id}"]
        queued = console.command("run echo inert", sentinel="")
        task_id = queued[0].rsplit(" ", 1)[-1]
        task = bot.poll("127.0.0.1", bot_port, bot_id, 2)
        assert task == {"task_id": task_id, "command": "echo inert"}
        assert bot.submit_result(
            "127.0.0.1",
            bot_port,
            bot_id,
            {"task_id": task_id, "outcome": "recorded"},
            2,
        )
        status = json.loads(console.command(f"status {bot_id}", sentinel="")[0])
        assert status["results"] == [{"task_id": task_id, "outcome": "recorded"}]


@pytest.mark.parametrize("delivery", ["tftp"])
def test_bot_probes_reports_and_fetches_only_inert_marker(
    tcp_servers, delivery, capsys
):
    class TargetHandler(socketserver.BaseRequestHandler):
        def handle(self):
            pass

    class ListenerHandler(socketserver.BaseRequestHandler):
        def handle(self):
            listener.handle(
                self.request,
                self.client_address,
                {"active": 0},
                threading.Lock(),
            )

    target_port = tcp_servers(TargetHandler)
    listener_port = tcp_servers(ListenerHandler)
    tftp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tftp.bind(("127.0.0.1", 0))
    tftp.settimeout(2)
    loader_port = tftp.getsockname()[1]

    def answer_tftp():
        request, peer = tftp.recvfrom(1024)
        assert request.startswith(b"\x00\x01mirai.synthetic\x00octet\x00")
        tftp.sendto(b"\x00\x03\x00\x01" + loader.STUB, peer)
        acknowledgement, _ = tftp.recvfrom(1024)
        assert acknowledgement == b"\x00\x04\x00\x01"

    tftp_thread = threading.Thread(target=answer_tftp)
    tftp_thread.start()
    try:
        task = {
            "task_id": "task-0001",
            "command": json.dumps(
                {
                    "op": "probe",
                    "target": "127.0.0.1",
                    "target_port": target_port,
                    "listener_host": "127.0.0.1",
                    "listener_port": listener_port,
                    "loader_host": "127.0.0.1",
                    "loader_port": loader_port,
                    "delivery": delivery,
                    "payload_path": "/mirai.synthetic",
                }
            ),
        }
        result = bot.execute_task(task, "bot-0001", [], 2)
        assert result["outcome"] == "complete"
        assert result["reachable"] and result["reported"] and result["loader_stub"]
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        report = next(row for row in records if row["event"] == "bot_report")
        assert report["source"] == "127.0.0.1"
        assert report["payload"]["reachable"] is True
        assert "vulnerable" not in report["payload"]
    finally:
        tftp_thread.join(timeout=2)
        tftp.close()


def test_bot_retries_checkin_and_does_not_probe_while_idle(monkeypatch):
    attempts = []

    def check_in(*args):
        attempts.append("checkin")
        return None if len(attempts) == 1 else "bot-0001"

    def poll(*args):
        attempts.append("poll")
        raise KeyboardInterrupt

    monkeypatch.setenv("MIRAI_CNC_HOST", "127.0.0.1")
    monkeypatch.setenv("BOT_TARGETS", "127.0.0.1:23")
    monkeypatch.setattr(bot, "check_in", check_in)
    monkeypatch.setattr(bot, "poll", poll)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        bot, "probe", lambda *args: (_ for _ in ()).throw(AssertionError("probed"))
    )
    with pytest.raises(KeyboardInterrupt):
        bot.main()
    assert attempts == ["checkin", "checkin", "poll"]


def test_bot_does_not_report_success_when_loader_is_missing(monkeypatch):
    monkeypatch.setattr(bot, "probe", lambda *args: True)
    monkeypatch.setattr(bot, "report", lambda *args: True)
    monkeypatch.setattr(
        bot,
        "fetch_stub",
        lambda *args: (_ for _ in ()).throw(OSError("loader unavailable")),
    )
    action = {
        "op": "probe",
        "target": "127.0.0.1",
        "target_port": 23,
        "listener_host": "127.0.0.1",
        "listener_port": 48101,
        "loader_host": "127.0.0.1",
        "loader_port": 80,
        "delivery": "http",
        "payload_path": "/mirai.synthetic",
    }
    result = bot.execute_task(
        {"task_id": "task-0002", "command": json.dumps(action)}, "bot-0001", [], 2
    )
    assert result["outcome"] == "failed"
    assert result["reported"] and not result["loader_stub"]


def test_scenario_observes_real_bot_completion(tcp_servers):
    registry = cnc.Registry()

    class CncBotHandler(socketserver.BaseRequestHandler):
        def handle(self):
            cnc.handle_bot(registry, self.request, self.client_address)

    class CncOperatorHandler(socketserver.BaseRequestHandler):
        def handle(self):
            cnc.handle_operator(registry, self.request)

    class TargetHandler(socketserver.BaseRequestHandler):
        def handle(self):
            pass

    class ListenerHandler(socketserver.BaseRequestHandler):
        def handle(self):
            listener.handle(
                self.request,
                self.client_address,
                {"active": 0},
                threading.Lock(),
            )

    class LoaderHandler(socketserver.BaseRequestHandler):
        def handle(self):
            loader.handle_http(self.request, self.client_address)

    bot_port = tcp_servers(CncBotHandler)
    operator_port = tcp_servers(CncOperatorHandler)
    target_port = tcp_servers(TargetHandler)
    listener_port = tcp_servers(ListenerHandler)
    loader_port = tcp_servers(LoaderHandler)
    bot_id = bot.check_in("127.0.0.1", bot_port, "lab", 2)
    assert bot_id
    stop = threading.Event()

    def drive_bot():
        while not stop.is_set():
            task = bot.poll("127.0.0.1", bot_port, bot_id, 2)
            if task is not None:
                result = bot.execute_task(task, bot_id, [], 2)
                assert bot.submit_result("127.0.0.1", bot_port, bot_id, result, 2)
            time.sleep(0.01)

    worker = threading.Thread(target=drive_bot, daemon=True)
    worker.start()

    class Run:
        def __init__(self):
            self.events = []

        def emit(self, event, **fields):
            self.events.append({"event": event, **fields})

    run = Run()
    try:
        dispatched = choreography.play_choreography(
            run,
            cnc_host="127.0.0.1",
            cnc_port=operator_port,
            bot_node="BOT-A",
            bot_host="127.0.0.1",
            listener_host="127.0.0.1",
            listener_port=listener_port,
            loader_host="127.0.0.1",
            loader_port=loader_port,
            verified_targets=[("127.0.0.1", target_port)],
            delivery="http",
            interval=0.02,
            wait_seconds=3,
            command="echo inert",
            payload_path="/mirai.synthetic",
            timeout=2,
        )
    finally:
        stop.set()
        worker.join(timeout=2)
    assert dispatched == 1
    assert any(
        row["event"] == "bot_task_result" and row["source"] == "BOT-A"
        for row in run.events
    )
    assert any(row["event"] == "inert_command_acknowledged" for row in run.events)
