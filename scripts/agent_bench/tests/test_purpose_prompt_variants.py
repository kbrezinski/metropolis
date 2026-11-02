"""Optional prompt conditions preserve the matched-purpose comparisons."""

import pytest

from agent_bench.prompt_conditions import CONDITION_IDS, render_prompt
from agent_bench.tasks import get_task


@pytest.mark.parametrize(
    ("base_id", "spanish_literals"),
    [
        ("ot-event", ("DNP3", "instrumento obsoleto", "30 segundos", "Modbus")),
        ("ot-backwash", ("filtración", "65", "90 kPa")),
    ],
)
def test_purpose_arms_render_the_same_request_under_every_condition(base_id, spanish_literals):
    arms = [get_task(f"{base_id}-{suffix}") for suffix in "abc"]
    for condition in CONDITION_IDS:
        rendered = [render_prompt(task, condition) for task in arms]
        assert rendered[0] == rendered[1] == rendered[2]

    spanish = render_prompt(arms[0], "spanish")
    for literal in spanish_literals:
        assert literal in spanish
    assert "solution.sh" in spanish
