"""Command line for building, checking, writing, and drawing topologies.

Examples::

    uv run python -m metropolis.topology show
    uv run python -m metropolis.topology show --dataset testbeds/metropolis/datasets/water_treatment_v1
    uv run python -m metropolis.topology check
    uv run python -m metropolis.topology render --format mermaid
    uv run python -m metropolis.topology render --format dot --out topology.dot
    uv run python -m metropolis.topology draw --out topology.png
    uv run python -m metropolis.topology export --out /tmp/regenerated

Reading and drawing need no arguments: with no ``--dataset`` the copy bundled
with the package is used, so this works from an installed distribution.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from metropolis.topology import Topology, TopologyError


def _load(args: argparse.Namespace) -> Topology:
    if args.dataset:
        return Topology.from_directory(args.dataset)
    return Topology.from_bundled()


def _format_summary(topology: Topology) -> str:
    return topology.summary()


def _write_or_print(text: str, out: str | None) -> int:
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"wrote {out}")
        return 0
    sys.stdout.write(text)
    return 0


def _render_graphviz(topology: Topology, args: argparse.Namespace) -> int:
    """Render through the ``dot`` program, which must be installed."""
    executable = shutil.which("dot")
    if executable is None:
        print(
            "Graphviz 'dot' is not on PATH. Write the source with "
            "--format dot and render it elsewhere, or install Graphviz.",
            file=sys.stderr,
        )
        return 2
    fmt = "svg" if args.format == "svg" else "png"
    completed = subprocess.run(
        [executable, f"-T{fmt}", "-o", args.out],
        input=topology.to_dot(args.title),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        print(completed.stderr.strip(), file=sys.stderr)
        return completed.returncode
    print(f"wrote {args.out}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    return _write_or_print(_format_summary(_load(args)), args.out)


def _cmd_check(args: argparse.Namespace) -> int:
    topology = _load(args)
    problems = topology.problems()
    print(topology.summary())
    print()
    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("No contradictions found.")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    topology = _load(args)
    if args.format == "mermaid":
        return _write_or_print(topology.to_mermaid(args.title), args.out)
    if args.format == "dot":
        return _write_or_print(topology.to_dot(args.title), args.out)
    if not args.out:
        print(f"--format {args.format} needs --out", file=sys.stderr)
        return 2
    return _render_graphviz(topology, args)


def _cmd_export(args: argparse.Namespace) -> int:
    topology = _load(args)
    problems = topology.problems()
    if problems and not args.force:
        print(
            f"refusing to write {len(problems)} contradictory topology problem(s); "
            "pass --force to write anyway",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    for path in topology.save(args.out):
        print(f"wrote {path}")
    return 0


def _cmd_draw(args: argparse.Namespace) -> int:
    """Draw the topology as an image, using networkx and matplotlib."""
    from metropolis.topology.draw import available, render

    if not available():
        print(
            "Drawing needs matplotlib and networkx. "
            "Install them with: uv pip install matplotlib networkx",
            file=sys.stderr,
        )
        return 2
    render(
        _load(args),
        args.out,
        title=args.title,
        dpi=args.dpi,
        note=args.note,
        seed=args.seed,
    )
    print(f"wrote {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metropolis.topology",
        description="Build, check, write, and draw Metropolis topologies.",
    )
    parser.add_argument(
        "--dataset",
        help="Dataset directory to read. Defaults to the copy bundled with the package.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("show", help="Print what the topology contains")
    show.add_argument("--out", help="Write to this file instead of stdout")
    show.set_defaults(func=_cmd_show)

    check = commands.add_parser("check", help="Report contradictions, non-zero if any")
    check.add_argument("--out", help="Write the summary to this file")
    check.set_defaults(func=_cmd_check)

    render = commands.add_parser("render", help="Draw the topology")
    render.add_argument(
        "--format",
        choices=["dot", "mermaid", "svg", "png"],
        default="mermaid",
        help="dot and mermaid need no extra software; svg and png need Graphviz",
    )
    render.add_argument("--out", help="Output file; required for svg and png")
    render.add_argument("--title", help="Title to draw at the top")
    render.set_defaults(func=_cmd_render)

    export = commands.add_parser("export", help="Write the dataset YAML documents")
    export.add_argument("--out", required=True, help="Dataset directory to create")
    export.add_argument(
        "--force", action="store_true", help="Write even if problems were found"
    )
    export.set_defaults(func=_cmd_export)

    draw = commands.add_parser(
        "draw", help="Draw an image of the topology, top to bottom"
    )
    draw.add_argument("--out", required=True, help="Image to write (.png, .svg, .pdf)")
    draw.add_argument("--title", help="Title across the top")
    draw.add_argument("--note", help="Small caption along the bottom")
    draw.add_argument(
        "--dpi", type=int, default=130, help="Resolution for raster output"
    )
    draw.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Layout seed, so the same command gives the same picture",
    )
    draw.set_defaults(func=_cmd_draw)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    except TopologyError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
