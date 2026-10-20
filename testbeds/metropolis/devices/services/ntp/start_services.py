"""Keep chronyd and the lab syslog receiver in the same service container."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time

STOP = False


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def main() -> int:
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    children = {}
    failed = False
    try:
        try:
            children["chronyd"] = subprocess.Popen(
                ["chronyd", "-d", "-x", "-f", "/etc/metropolis-chrony.conf"]
            )
            children["syslog"] = subprocess.Popen(
                [sys.executable, "-u", "/opt/metropolis/syslog_receiver.py"]
            )
        except OSError:
            failed = True
            print(json.dumps({"event": "service_start_failed"}), flush=True)
            return 1
        print(
            json.dumps({"event": "services_started", "services": sorted(children)}),
            flush=True,
        )
        while not STOP:
            for name, child in children.items():
                if child.poll() is not None:
                    print(
                        json.dumps(
                            {
                                "event": "service_exited",
                                "service": name,
                                "exit_code": child.returncode,
                            }
                        ),
                        flush=True,
                    )
                    failed = True
                    return 1
            time.sleep(0.2)
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for child in children.values():
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        print(json.dumps({"event": "services_stopped", "failed": failed}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
