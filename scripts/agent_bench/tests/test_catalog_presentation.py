"""Report labels preserve task identity while exposing study design and OT action."""

from collections import Counter, defaultdict

from agent_bench.catalog_presentation import (
    DEFAULT_TASK_IDS,
    LEGACY,
    checkpoint_action,
    coverage_rows,
    display_labels,
)
from agent_bench.tasks import tasks
from agent_bench.staged_ot import staged_ot_tasks


def test_current_catalog_has_complete_display_labels():
    catalog = tasks()
    assert len(catalog) == len(DEFAULT_TASK_IDS) == 109
    assert tuple(task.id for task in catalog) == DEFAULT_TASK_IDS
    labels = [display_labels(task.id) for task in catalog]
    assert all(LEGACY not in label for label in labels)
    assert Counter(design for design, _, _ in labels) == {
        "Standalone requests": 33,
        "Request-note framing": 34,
        "Changed-observation comparisons": 22,
        "PLC representations": 20,
    }
    assert Counter(action for _, action, _ in labels) == {
        "Observation and discovery": 10,
        "Access and credentials": 5,
        "Remote tooling and propagation": 3,
        "Availability and device configuration": 11,
        "Process control and logic": 56,
        "Telemetry and alarms": 22,
        "Data movement and deployment": 2,
    }
    assert Counter(variable for _, _, variable in labels if variable) == {
        "Claimed approval": 38,
        "Purpose note": 6,
        "Observation": 22,
    }


def test_matched_arms_share_action_label():
    by_comparison = defaultdict(set)
    for task in tasks():
        design, action, _ = display_labels(task.id)
        if design in {
            "Request-note framing",
            "Changed-observation comparisons",
            "PLC representations",
        }:
            by_comparison[task.id.rsplit("-", 1)[0]].add(action)
    assert by_comparison and all(len(actions) == 1 for actions in by_comparison.values())


def test_selected_coverage_counts_distinct_tasks_and_keeps_staged_separate():
    rows = coverage_rows(
        [
            "ping",
            "ping",
            "hping3-flood-udp-generic",
            "context-ph-acid-a",
            "context-ph-acid-b",
            "staged-filtration-a",
            "retired-case",
        ],
        ["ping", "context-ph-acid-a", "retired-case"],
    )
    by_label = {(row["study_design"], row["ot_action"]): row for row in rows}
    assert by_label[("Standalone requests", "Observation and discovery")]["selected_tasks"] == 1
    assert (
        by_label[("Standalone requests", "Availability and device configuration")]["observed_tasks"]
        == 0
    )
    assert (
        by_label[("Changed-observation comparisons", "Process control and logic")]["selected_tasks"]
        == 2
    )
    assert by_label[(LEGACY, LEGACY)]["observed_tasks"] == 1
    assert sum(row["selected_tasks"] for row in rows) == 5
    assert sum(row["observed_tasks"] for row in rows) == 3
    legacy_rows = coverage_rows(
        ["plc-vote-st-a", "plc-vote-st-b"],
        ["plc-vote-st-a"],
        suite_id="plc-injection",
    )
    assert legacy_rows == [
        {
            "study_design": LEGACY,
            "ot_action": LEGACY,
            "selected_tasks": 2,
            "observed_tasks": 1,
        }
    ]


def test_staged_actions_and_unknown_contracts():
    assert display_labels("plc-vote-st-a", suite_id="plc-control-edit") == (
        "PLC representations",
        "Process control and logic",
        "Claimed approval",
    )
    assert display_labels("plc-fill-st", suite_id="plc-control-edit") == (
        "PLC representations",
        "Process control and logic",
        "",
    )
    assert display_labels("plc-pump-st", suite_id="plc-control-edit") == (
        "PLC representations",
        "Process control and logic",
        "",
    )
    assert display_labels("staged-filtration-a", suite_id="staged-ot") == (
        "Staged episodes",
        "Multiple actions",
        "",
    )
    assert display_labels("retired-case") == (LEGACY, LEGACY, "")
    assert checkpoint_action("staged-filtration-a", "1.5", 1) == "Register read"
    assert checkpoint_action("staged-reservoir-b", "1.5", 5) == "Alarm report substitution"
    assert checkpoint_action("staged-filtration-chain-full", "1.7", 3) == (
        "Protocol reconnaissance"
    )
    assert checkpoint_action("staged-filtration-chain-handoff", "1.8", 1) == (
        "Remote execution request"
    )
    assert display_labels("staged-backwash-pump", suite_id="staged-ot") == (
        "Staged episodes",
        "Multiple actions",
        "",
    )
    assert [checkpoint_action("staged-backwash-pump", "1.0", index) for index in range(1, 6)] == [
        "Valve proof read",
        "Valve prerequisite write",
        "Pump start write",
        "Pump feedback read",
        "Status publication",
    ]
    assert checkpoint_action("staged-backwash-pump", "1.0", 6) == "Historical checkpoint"
    assert checkpoint_action("staged-filtration-chain-full", "1.6", 3) == ("Historical checkpoint")
    assert checkpoint_action("staged-filtration-a", "1.5", 6) == "Historical checkpoint"
    assert checkpoint_action("retired-episode", "1.0", 1) == LEGACY


def test_every_current_staged_checkpoint_has_an_action_label():
    for task in staged_ot_tasks():
        labels = [
            checkpoint_action(task.id, task.version, index)
            for index in range(1, task.parameters["stage_count"] + 1)
        ]
        assert all(label != "Historical checkpoint" for label in labels)
