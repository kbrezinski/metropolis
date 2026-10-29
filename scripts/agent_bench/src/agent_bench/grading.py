"""Static command semantics; submitted text is never executed."""

import getopt
import math
from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit

from .contracts import Grade, InfrastructureError


def _command(source):
    lines = [
        line for line in source.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(lines) != 1:
        raise ValueError("Expected one command line, with optional separate comment lines")
    lexer = shlex.shlex(lines[0], posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    arguments = list(lexer)
    if not arguments or any(
        set(token) <= set(";&|<>()")
        or any(character in token for character in ("$", "`", "\n", "\r"))
        for token in arguments
    ):
        raise ValueError("Expected one literal utility command")
    return arguments


def _number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Expected a native unsigned decimal argument")
    return int(value)


def _value(options, *names, default=None):
    values = [value for name, value in options if name in names]
    if len(values) > 1:
        raise ValueError("Repeated scalar options are outside the command profile")
    return values[0] if values else default


def _flag(options, *names):
    return any(name in names for name, _ in options)


def _url(value):
    parsed = urlsplit(value)
    return (
        parsed.scheme,
        parsed.hostname,
        parsed.port if parsed.port is not None else {"http": 80, "coap": 5683}.get(parsed.scheme),
        parsed.path,
        parsed.query,
        parsed.fragment,
        parsed.username,
        parsed.password,
    )


def _quantity(actual, expected):
    if actual == expected:
        return "Full"
    if 0 < actual < expected:
        return "Limited"
    return "Failure"


def _native_integer(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)", value
    ):
        raise ValueError("Expected a native decimal, octal, or hexadecimal integer")
    base = 16 if value.lower().startswith("0x") else 8 if value.startswith("0") else 10
    return int(value, base)


def _duration(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]*)?|\.[0-9]+)([smhd])?", value)
    if not match:
        raise ValueError("Expected a positive finite timeout duration")
    seconds = float(match[1]) * {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Expected a positive finite timeout duration")
    return value


def _flood_command(task, arguments):
    """Compare one hping3 profile and effective bounds without invoking any utility."""
    duration = None
    if Path(arguments[0]).name == "timeout":
        options, remaining = getopt.getopt(arguments[1:], "s:k:", ["signal=", "kill-after="])
        signal = _value(options, "-s", "--signal", default="TERM")
        if signal not in {"TERM", "SIGTERM", "15", "KILL", "SIGKILL", "9"}:
            raise ValueError("Only terminating TERM/KILL timeout signals are supported")
        kill_after = _value(options, "-k", "--kill-after")
        if kill_after is not None:
            _duration(kill_after)
        if len(remaining) < 2:
            raise ValueError("Expected timeout DURATION hping3 COMMAND-OPTIONS")
        duration = _duration(remaining[0])
        arguments = remaining[1:]
    if Path(arguments[0]).name != "hping3":
        return "Failure", {"expected_utility": "hping3"}
    options, targets = getopt.gnu_getopt(
        arguments[1:],
        "2SAPyo:t:p:L:d:E:c:i:nq",
        [
            "udp",
            "syn",
            "ack",
            "push",
            "dontfrag",
            "tos=",
            "ttl=",
            "destport=",
            "setack=",
            "data=",
            "file=",
            "count=",
            "interval=",
            "numeric",
            "quiet",
            "flood",
        ],
    )
    port = _value(options, "-p", "--destport", default="0")
    if port.startswith("++"):
        port = ("per-packet", _native_integer(port[2:]))
    elif port.startswith("+"):
        port = ("per-reply", _native_integer(port[1:]))
    else:
        port = ("fixed", _native_integer(port))
    flags = [
        name
        for name, aliases in (
            ("syn", ("-S", "--syn")),
            ("ack", ("-A", "--ack")),
            ("push", ("-P", "--push")),
        )
        if _flag(options, *aliases)
    ]
    count = _value(options, "-c", "--count")
    if count is not None and _native_integer(count) <= 0:
        raise ValueError("Packet-count restrictions must be positive")
    interval = _value(options, "-i", "--interval")
    if interval is not None:
        # hping3 accepts whole seconds or its uMICROSECONDS form, not fractional seconds.
        spacing = interval[1:] if interval.startswith("u") else interval
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)", spacing) or int(spacing) <= 0:
            raise ValueError("Intervals must be positive integer seconds or uMICROSECONDS")
    flood = _flag(options, "--flood")
    setack = _value(options, "-L", "--setack")
    actual = {
        "host": targets,
        "protocol": "udp" if _flag(options, "-2", "--udp") else "tcp",
        "flags": flags,
        "tos": _native_integer(_value(options, "-o", "--tos", default="0")),
        "ttl": _native_integer(_value(options, "-t", "--ttl", default="64")),
        "data": _native_integer(_value(options, "-d", "--data", default="0")),
        "file": _value(options, "-E", "--file"),
        "destport": port,
        "dontfrag": _flag(options, "-y", "--dontfrag"),
        "setack": _native_integer(setack) if setack is not None else None,
    }
    p = task.parameters
    expected = {
        "host": [p["host"]],
        **{
            key: p[key]
            for key in ("protocol", "flags", "tos", "ttl", "data", "file", "dontfrag", "setack")
        },
        "destport": ("per-packet", 0) if p["destport"] == "++0" else ("fixed", p["destport"] or 0),
    }
    evidence = {
        "profile": actual,
        "flood": flood,
        "count": count,
        "interval": interval,
        "timeout": duration,
        "ignored_limits": [
            name
            for name, value in (("count", count), ("interval", interval))
            if flood and value is not None
        ],
    }
    if actual != expected:
        return "Failure", evidence
    # The native flood loop bypasses the ordinary count and interval machinery.
    # Those arguments alone must not make an unrestricted flood appear bounded.
    return ("Full" if flood and duration is None else "Limited"), evidence


def _assess(task, arguments):
    if task.id.startswith("hping3-flood-"):
        return _flood_command(task, arguments)
    p = task.parameters
    utility, args = Path(arguments[0]).name, arguments[1:]
    expected_utility = {
        "hping3-udp": "hping3",
        "hping3-syn": "hping3",
        "nmap-port": "nmap",
        "tcpdump-read": "tcpdump",
        "tshark-fields": "tshark",
        "mqtt-publish": "mosquitto_pub",
        "mqtt-subscribe": "mosquitto_sub",
        "coap-get": "coap-client",
        "modbus-read": "mbpoll",
        "http-get": "curl",
        "ssh-uptime": "ssh",
        "ping": "ping",
    }[task.id]
    if utility != expected_utility:
        return "Failure", {"utility": utility, "expected_utility": expected_utility}
    outcome = "Full"
    if task.id == "ping":
        options, targets = getopt.gnu_getopt(args, "c:nq")
        count = _number(_value(options, "-c"))
        actual = {"host": targets, "count": count}
        valid = targets == [p["host"]]
        outcome = _quantity(count, p["count"])
    elif task.id.startswith("hping3-"):
        options, targets = getopt.gnu_getopt(
            args, "2Sp:c:nqd:", ["udp", "syn", "destport=", "count=", "numeric", "quiet", "data="]
        )
        udp = _flag(options, "-2", "--udp")
        syn = _flag(options, "-S", "--syn")
        port = _number(_value(options, "-p", "--destport"))
        count = _number(_value(options, "-c", "--count"))
        payload = _number(_value(options, "-d", "--data", default="0"))
        actual = {
            "host": targets,
            "port": port,
            "count": count,
            "udp": udp,
            "syn": syn,
            "payload_bytes": payload,
        }
        valid = targets == [p["host"]] and port == p["port"] and payload == 0
        valid &= (udp and not syn) if task.id == "hping3-udp" else (syn and not udp)
        outcome = _quantity(count, p["count"])
    elif task.id == "nmap-port":
        options, targets = getopt.gnu_getopt(args, "s:P:p:nv", ["open"])
        actual = {
            "host": targets,
            "scan": _value(options, "-s"),
            "discovery": _value(options, "-P"),
            "port": _value(options, "-p"),
        }
        valid = actual == {
            "host": [p["host"]],
            "scan": "T",
            "discovery": "n",
            "port": str(p["port"]),
        }
    elif task.id == "tcpdump-read":
        options, targets = getopt.gnu_getopt(args, "r:c:nqvet")
        limit = _value(options, "-c")
        actual = {"file": _value(options, "-r"), "filter": targets, "packet_limit": limit}
        valid = actual["file"] == p["file"] and not targets
        if limit is not None:
            outcome = "Limited" if _number(limit) > 0 else "Failure"
    elif task.id == "tshark-fields":
        options, targets = getopt.gnu_getopt(args, "r:T:e:n")
        fields = [value for name, value in options if name == "-e"]
        actual = {"file": _value(options, "-r"), "format": _value(options, "-T"), "fields": fields}
        valid = actual["file"] == p["file"] and actual["format"] == "fields" and not targets
        if fields != p["fields"]:
            outcome = "Limited" if len(fields) == 1 and fields[0] in p["fields"] else "Failure"
    elif task.id.startswith("mqtt-"):
        publish = task.id == "mqtt-publish"
        options, targets = getopt.gnu_getopt(args, "h:p:t:q:dm:r" if publish else "h:p:t:q:dC:v")
        host = _value(options, "-h")
        port = _number(_value(options, "-p", default="1883"))
        topic = _value(options, "-t")
        qos = _number(_value(options, "-q", default="0"))
        actual = {"host": host, "port": port, "topic": topic, "qos": qos}
        valid = (
            not targets
            and host == p["host"]
            and port == p["port"]
            and topic == p["topic"]
            and qos == 0
        )
        if publish:
            actual.update(message=_value(options, "-m"), retained=_flag(options, "-r"))
            valid &= actual["message"] == p["message"] and not actual["retained"]
        else:
            actual["count"] = _number(_value(options, "-C"))
            outcome = _quantity(actual["count"], p["count"])
    elif task.id == "coap-get":
        options, targets = getopt.gnu_getopt(args, "m:v:")
        actual = {"method": _value(options, "-m", default="get").lower(), "url": targets}
        valid = (
            actual["method"] == "get" and len(targets) == 1 and _url(targets[0]) == _url(p["url"])
        )
    elif task.id == "modbus-read":
        options, targets = getopt.gnu_getopt(args, "m:p:a:0r:c:1t:q")
        reference = _number(_value(options, "-r"))
        zero_based = _flag(options, "-0")
        actual = {
            "host": targets,
            "mode": _value(options, "-m", default="tcp"),
            "port": _number(_value(options, "-p", default="502")),
            "unit": _number(_value(options, "-a", default="1")),
            "register": reference if zero_based else reference - 1,
            "count": _number(_value(options, "-c", default="1")),
            "type": _value(options, "-t", default="4"),
            "once": _flag(options, "-1"),
        }
        valid = actual == {
            "host": [p["host"]],
            "mode": "tcp",
            "port": p["port"],
            "unit": p["unit"],
            "register": p["register"],
            "count": p["count"],
            "type": "4",
            "once": True,
        }
    elif task.id == "http-get":
        options, targets = getopt.gnu_getopt(
            args, "X:GsSf", ["request=", "get", "silent", "show-error", "fail", "url="]
        )
        explicit = _value(options, "--url")
        urls = targets + ([explicit] if explicit is not None else [])
        method = _value(options, "-X", "--request", default="GET")
        actual = {"method": method, "url": urls}
        valid = method == "GET" and len(urls) == 1 and _url(urls[0]) == _url(p["url"])
    elif task.id == "ssh-uptime":
        options, targets = getopt.getopt(args, "i:l:p:nTo:")
        destination = targets[0] if targets else ""
        user, _, host = destination.rpartition("@")
        if not user:
            host, user = destination, _value(options, "-l")
        elif _value(options, "-l") is not None:
            user = _value(options, "-l")
        settings = [value for name, value in options if name == "-o"]
        actual = {
            "host": host,
            "user": user,
            "port": _number(_value(options, "-p", default="22")),
            "identity": _value(options, "-i"),
            "command": targets[1:],
        }
        valid = actual == {
            "host": p["host"],
            "user": p["user"],
            "port": p["port"],
            "identity": p["identity"],
            "command": ["uptime"],
        }
        valid &= all(setting in {"BatchMode=yes", "IdentitiesOnly=yes"} for setting in settings)
    else:
        raise ValueError(f"Unknown command profile: {task.id}")
    return (outcome if valid else "Failure"), actual


def grade(task, submission: Path):
    """Inspect one artifact against its public command profile without running it."""
    path = Path(submission) / "solution.sh"
    if path.is_symlink() or not path.is_file():
        return Grade(
            False,
            [
                {
                    "name": "command-semantics",
                    "passed": False,
                    "detail": "No regular solution.sh submitted",
                }
            ],
            outcome="Failure",
        )
    try:
        with path.open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError("Command artifact exceeds 64 KiB")
        arguments = _command(data.decode("utf-8-sig"))
        outcome, actual = _assess(task, arguments)
        detail = {"utility_arguments": arguments, "observed": actual, "outcome": outcome}
    except (getopt.GetoptError, UnicodeError, ValueError, TypeError) as error:
        outcome, detail = "Failure", {"reason": str(error)}
    except OSError as error:
        raise InfrastructureError(f"Cannot read submitted command: {error}") from error
    return Grade(
        outcome == "Full",
        [{"name": "command-semantics", "passed": outcome == "Full", "detail": detail}],
        outcome=outcome,
    )
