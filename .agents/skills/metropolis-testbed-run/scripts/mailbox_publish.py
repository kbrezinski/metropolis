"""Publish one response from inside the current mailbox."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid


MAILBOX = Path(__file__).resolve().parent
REQUEST_NAME = re.compile(r"request-(\d{3})\.json\Z")
SUMMARY_FIELDS = {
    "task_understanding",
    "assumptions",
    "restrictions",
    "substitutions",
    "refusal_reason",
}


def mailbox_file(name):
    """Accept only a plain file name in this participant's mailbox."""
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("Use a file name within this mailbox, not a path")
    path = MAILBOX / name
    if path.is_symlink():
        raise ValueError("Mailbox links are not accepted")
    return path


def load_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def current_request(name, *, summary):
    if (MAILBOX / "closed.json").exists():
        raise ValueError("This attempt is closed")
    request = mailbox_file(name)
    if summary:
        if name != "decision-summary-request.json":
            raise ValueError("Use decision-summary-request.json for the summary")
        response = MAILBOX / "decision-summary-response.json"
    else:
        match = REQUEST_NAME.fullmatch(name)
        if match is None:
            raise ValueError("Use the current request-NNN.json file")
        if (MAILBOX / "decision-summary-request.json").exists():
            raise ValueError("Generation has ended; only the summary remains")
        latest = max(
            (
                int(found.group(1))
                for path in MAILBOX.glob("request-*.json")
                if (found := REQUEST_NAME.fullmatch(path.name)) is not None
            ),
            default=0,
        )
        if int(match.group(1)) != latest:
            raise ValueError("That request is stale; use the latest request-NNN.json")
        response = MAILBOX / f"response-{match.group(1)}.json"
    if response.exists():
        raise FileExistsError(f"{response.name} was already published")
    payload = load_object(request)
    if not all(
        isinstance(payload.get(key), str) and payload[key]
        for key in ("attempt_nonce", "request_id")
    ):
        raise ValueError("Current request is missing its attempt nonce or request ID")
    return payload, response


def require_tool(request, name):
    tools = request.get("tools")
    if not isinstance(tools, list) or name not in [
        tool.get("name") for tool in tools if isinstance(tool, dict)
    ]:
        raise ValueError(f"The current request does not offer {name}")


def command_text(args):
    if args.command_file is None:
        command = args.command
    else:
        command = mailbox_file(args.command_file).read_text(encoding="utf-8")
        if command.endswith("\r\n"):
            command = command[:-2]
        elif command.endswith("\n"):
            command = command[:-1]
    if not command or not command.strip():
        raise ValueError("Provide one nonempty artifact line")
    if command.endswith(("`n", "\\n")):
        raise ValueError(
            "The artifact ends with literal `n or \\n; omit it, as a real newline is added automatically"
        )
    if "\r" in command or "\n" in command:
        raise ValueError("Provide exactly one artifact line")
    return command + "\n"


def edited_program_text(args):
    if args.command_file is None:
        raise ValueError("edited_program.xml requires --content-file")
    with mailbox_file(args.command_file).open("rb") as stream:
        content = stream.read(65_537)
    if len(content) > 65_536:
        raise ValueError("edited_program.xml exceeds 65536 bytes")
    if b"\x00" in content:
        raise ValueError("edited_program.xml contains a NUL byte")
    program = content.decode("utf-8")
    if not program.strip():
        raise ValueError("Provide a nonempty edited_program.xml")
    return program


def multiline_solution_text(args):
    if args.command_file is None:
        content = args.command
    else:
        with mailbox_file(args.command_file).open("rb") as stream:
            data = stream.read(65_537)
        if len(data) > 65_536:
            raise ValueError("solution.sh exceeds 65536 bytes")
        content = data.decode("utf-8")
    if not content or not content.strip():
        raise ValueError("Provide a nonempty solution.sh")
    if "\x00" in content or len(content.encode("utf-8")) > 65_536:
        raise ValueError("solution.sh contains a NUL byte or exceeds 65536 bytes")
    return content


def expected_artifact(request):
    path = request.get("artifact_path", "solution.sh")
    if path not in {"solution.sh", "edited_program.xml"}:
        raise ValueError("Current request has an unsupported artifact path")
    return path


def summary_value(args):
    if (args.draft is not None or args.unavailable is not None) and (
        args.assumption
        or args.restriction
        or args.substitution
        or args.refusal_reason is not None
    ):
        raise ValueError("Inline summary fields require --task-understanding")
    if args.unavailable is not None:
        if not args.unavailable.strip():
            raise ValueError("Give a brief reason when the summary is unavailable")
        return {"summary": None, "reason": args.unavailable}
    summary = (
        load_object(mailbox_file(args.draft))
        if args.draft is not None
        else {
            "task_understanding": args.task_understanding,
            "assumptions": args.assumption,
            "restrictions": args.restriction,
            "substitutions": args.substitution,
            "refusal_reason": args.refusal_reason,
        }
    )
    if set(summary) != SUMMARY_FIELDS:
        raise ValueError("Summary draft must have the five requested fields")
    if not isinstance(summary["task_understanding"], str) or not (
        summary["refusal_reason"] is None or isinstance(summary["refusal_reason"], str)
    ):
        raise ValueError(
            "Summary text and refusal reason must be text or null as requested"
        )
    for name in ("assumptions", "restrictions", "substitutions"):
        if not isinstance(summary[name], list) or not all(
            isinstance(item, str) for item in summary[name]
        ):
            raise ValueError(f"Summary {name} must be a list of strings")
    if len(json.dumps(summary, ensure_ascii=False)) > 3000:
        raise ValueError("Summary exceeds 3000 characters")
    return {"summary": summary}


def publish_once(path, value):
    data = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()
    temporary = MAILBOX / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
        # Windows rename does not replace an existing file; POSIX hard-link creation
        # provides the same atomic no-replace property on that platform.
        if os.name == "nt":
            temporary.rename(path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("ready")
    for mode in (
        "show",
        "write",
        "read",
        "submit-stage",
        "finish",
        "refuse",
        "summary",
    ):
        item = modes.add_parser(mode)
        item.add_argument(
            "--request", required=True, help="Current request file name in this mailbox"
        )
        if mode == "write":
            item.add_argument("--path", help="Artifact path advertised in this request")
            source = item.add_mutually_exclusive_group(required=True)
            source.add_argument("--content", "--command", dest="command")
            source.add_argument(
                "--content-file",
                "--command-file",
                dest="command_file",
                help="Mailbox file containing artifact text",
            )
        elif mode == "read":
            item.add_argument("--path", required=True)
        elif mode == "finish":
            item.add_argument("--reason", required=True)
            item.add_argument("--path", required=True, action="append")
        elif mode == "refuse":
            item.add_argument("--reason", required=True)
        elif mode == "summary":
            source = item.add_mutually_exclusive_group(required=True)
            source.add_argument(
                "--task-understanding",
                help="Brief task understanding without a draft file",
            )
            source.add_argument(
                "--draft", help="Mailbox JSON file with five summary fields"
            )
            source.add_argument("--unavailable", help="Reason no summary can be given")
            item.add_argument("--assumption", action="append", default=[])
            item.add_argument("--restriction", action="append", default=[])
            item.add_argument("--substitution", action="append", default=[])
            item.add_argument("--refusal-reason")
    return parser


def run(args):
    if args.mode == "ready":
        if (MAILBOX / "closed.json").exists() or any(MAILBOX.glob("request-*.json")):
            raise ValueError("Mailbox readiness must precede the first request")
        challenge = load_object(mailbox_file("ready-challenge.json"))
        if set(challenge) != {"attempt_nonce", "challenge"} or not all(
            isinstance(value, str) and value for value in challenge.values()
        ):
            raise ValueError("Invalid mailbox readiness challenge")
        publish_once(MAILBOX / "ready.json", challenge)
        print("Published ready.json")
        return
    request, response = current_request(args.request, summary=args.mode == "summary")
    common = {
        "attempt_nonce": request["attempt_nonce"],
        "request_id": request["request_id"],
    }
    if args.mode == "show":
        number = REQUEST_NAME.fullmatch(args.request).group(1)
        seen_path = MAILBOX / f"seen-{number}.json"
        seen = {
            **common,
            "request_sha256": hashlib.sha256(
                mailbox_file(args.request).read_bytes()
            ).hexdigest(),
        }
        if seen_path.exists():
            if load_object(seen_path) != seen:
                raise ValueError("Existing request receipt does not match this request")
        else:
            publish_once(seen_path, seen)
        print(json.dumps(request, ensure_ascii=False, indent=2))
        return
    if args.mode == "summary":
        payload = {**common, **summary_value(args)}
    else:
        if args.mode == "refuse":
            if not args.reason.strip():
                raise ValueError("Give a reason for the refusal")
            payload = {
                **common,
                "content": args.reason,
                "tool_calls": [],
                "provider_refusal": True,
            }
        else:
            name = {
                "write": "write_file",
                "read": "read_file",
                "submit-stage": "submit_stage",
                "finish": "finish",
            }[args.mode]
            require_tool(request, name)
            if args.mode == "write":
                artifact = expected_artifact(request)
                if args.path is not None and args.path != artifact:
                    raise ValueError(f"This request accepts only {artifact}")
                multiline = request.get("allow_multiline_solution", False)
                if type(multiline) is not bool:
                    raise ValueError("Current request has invalid multiline permission")
                if multiline and artifact != "solution.sh":
                    raise ValueError(
                        "Multiline solution permission is not valid for this task"
                    )
                content = (
                    edited_program_text(args)
                    if artifact == "edited_program.xml"
                    else multiline_solution_text(args)
                    if multiline
                    else command_text(args)
                )
                arguments = {"path": artifact, "content": content}
            elif args.mode == "read":
                arguments = {"path": args.path}
            elif args.mode == "submit-stage":
                arguments = {}
            else:
                arguments = {"reason": args.reason, "paths": args.path}
            payload = {
                **common,
                "content": "",
                "tool_calls": [{"name": name, "arguments": arguments}],
                "provider_refusal": False,
            }
    publish_once(response, payload)
    print(f"Published {response.name}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except (OSError, ValueError, UnicodeError) as error:
        print(f"mailbox_publish: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
