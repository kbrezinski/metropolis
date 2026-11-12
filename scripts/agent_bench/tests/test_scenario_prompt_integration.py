"""Scenario conditions and reports preserve evidence and stage boundaries."""

from agent_bench.prompt_conditions import CONDITION_IDS, render_prompt
from agent_bench.tasks import get_task, tasks


def test_scenario_variants_do_not_reveal_evidence_only_values():
    scenarios = [task for task in tasks() if task.family == "offensive-scenario"]
    for task in scenarios:
        hidden = [
            value
            for key in (
                "host",
                "topic",
                "message",
                "url",
                "module",
                "binary",
                "agent_id",
                "target_host",
                "loader_url",
                "output_path",
            )
            if isinstance(value := task.parameters.get(key), str) and value not in task.prompt
        ]
        if intent := task.parameters.get("intent"):
            hidden.extend(
                value
                for key in ("source", "target")
                if isinstance(value := intent.get(key), str) and value not in task.prompt
            )
        for condition in CONDITION_IDS:
            rendered = render_prompt(task, condition)
            assert all(path in rendered for path in task.starter_files), (task.id, condition)
            assert all(value not in rendered for value in hidden), (task.id, condition)


def test_context_pair_requests_stay_identical_under_every_condition():
    for name in (
        "plc",
        "dosing",
        "telnet",
        "mitm",
        "master",
        "alarm",
        "reflection",
        "mqtt-scope",
        "chlorine",
        "pump-guard",
        "drive-speed",
        "trip-alarm",
        "ph-acid",
        "vfd-current",
        "vfd-extreme",
        "ph-acid-command",
        "vfd-current-command",
        "vfd-extreme-command",
    ):
        first = get_task(f"context-{name}-a")
        second = get_task(f"context-{name}-b")
        for condition in CONDITION_IDS:
            assert render_prompt(first, condition) == render_prompt(second, condition)
        spanish = render_prompt(first, "spanish").casefold()
        assert not any(word in spanish for word in ("política", "prohibido", "permiso"))
