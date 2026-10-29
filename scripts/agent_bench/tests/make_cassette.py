"""Create a free, deterministic pipeline-check cassette from trusted test solutions."""

import argparse
import json
from pathlib import Path

from reference_solutions import reference_files
from agent_bench.tasks import tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() or args.output.with_suffix(".toml").exists():
        parser.error("Choose a new cassette output path")
    responses = {}
    for task in tasks():
        edits = [
            {"name": "write_file", "arguments": {"path": path, "content": content}}
            for path, content in reference_files(task.id).items()
        ]
        responses[task.id] = [
            {
                "content": "Write trusted reference command (pipeline check only).",
                "tool_calls": edits,
            },
            {"content": "Reference submission complete; independent grading has not run yet."},
        ]
    args.output.write_text(
        json.dumps({"schema_version": "1.0", "tasks": responses}, indent=2) + "\n", encoding="utf-8"
    )
    config = args.output.with_suffix(".toml")
    config.write_text(
        '[models.canned]\nprovider = "canned"\nid = "reference-pipeline-check"\n'
        + f"cassette = {json.dumps(args.output.name)}\n",
        encoding="utf-8",
    )
    print(f"Created {args.output} and {config}. These are not model results.")


if __name__ == "__main__":
    main()
