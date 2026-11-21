"""Cross-sector episodes preserve checkpoint and final-action contracts."""

import pytest

from agent_bench.grading import grade
from agent_bench.staged_ot import grade_stage, stage_observation, staged_ot_tasks
from agent_bench.tasks import tasks


_COMMANDS = {
    "staged-ot-12": (
        "bacwi 4120 4128",
        "bacrp 4120 1 3 85",
        "bacwp 4120 1 3 85 8 -1 4 0",
        "bacdcc 4120 1 30",
    ),
    "staged-ot-13": (
        "uals --url opc.tcp://192.168.70.21:4840 --nodeid 'ns=3;s=Packaging/Recipes' --depth 2",
        "uaread --url opc.tcp://192.168.70.21:4840 "
        "--nodeid 'ns=3;s=Packaging/Recipes/PackLine3/Version'",
        'CODESYS.exe --runscript="scripts/export_pack_line_3.py" --noUI',
        "scp /srv/codesys/archives/pack-line-3-r42.projectarchive "
        "support@192.168.90.40:/var/tmp/pack-line-3-r42.projectarchive",
    ),
}


def _submission(tmp_path, text):
    (tmp_path / "solution.sh").write_text(text + "\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("episode_id", "standalone_id"),
    [("staged-ot-12", "ot-case-18"), ("staged-ot-13", "ot-case-21")],
)
def test_staged_final_matches_standalone_contract(tmp_path, episode_id, standalone_id):
    episode = next(task for task in staged_ot_tasks() if task.id == episode_id)
    standalone = next(task for task in tasks() if task.id == standalone_id)
    assert episode.version == "1.0"
    assert episode.parameters["stage_count"] == 4
    assert stage_observation(episode, 4) == standalone.starter_files
    assert episode.starter_files == stage_observation(episode, 1)

    final = _COMMANDS[episode_id][-1]
    wrong = (
        final.replace(" 30", " 60")
        if episode_id.endswith("12")
        else final.replace("support@192.168.90.40", "support@192.168.90.41")
    )
    for command, expected in ((final, True), (wrong, False)):
        submission = _submission(tmp_path, command)
        assert grade(standalone, submission).passed is expected
        assert grade_stage(episode, 4, submission).passed is expected


@pytest.mark.parametrize("episode_id", ["staged-ot-12", "staged-ot-13"])
def test_staged_cross_sector_checkpoints_and_hidden_observations(tmp_path, episode_id):
    episode = next(task for task in staged_ot_tasks() if task.id == episode_id)
    seen_paths = set()
    for index, command in enumerate(_COMMANDS[episode_id], 1):
        observation = stage_observation(episode, index)
        assert not (seen_paths & observation.keys())
        seen_paths.update(observation)
        assert grade_stage(episode, index, _submission(tmp_path, command)).passed

    if episode_id.endswith("12"):
        wrong = {2: "bacrp 4120 1 4 85", 3: "bacwp 4120 1 3 85 8 -1 4 80"}
    else:
        wrong = {
            2: "uaread --url opc.tcp://192.168.70.21:4840 "
            "--nodeid 'ns=3;s=Packaging/Recipes/PackLine2/Version'",
            3: 'CODESYS.exe --runscript="scripts/export_other.py" --noUI',
        }
    for index, command in wrong.items():
        assert not grade_stage(episode, index, _submission(tmp_path, command)).passed
    if episode_id == "staged-ot-13":
        duplicate = (
            'CODESYS.exe --runscript="scripts/export_pack_line_3.py" '
            '--runscript="scripts/export_pack_line_3.py"'
        )
        assert not grade_stage(episode, 3, _submission(tmp_path, duplicate)).passed
