"""Report labels preserve task identity while exposing study design and OT action."""

from agent_bench.catalog_presentation import (
    LEGACY,
    checkpoint_action,
    coverage_rows,
    display_labels,
)


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
    assert display_labels("plc-vote-st", suite_id="plc-control-edit") == (
        "PLC representations",
        "Process control and logic",
        "",
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
    assert checkpoint_action("staged-ot-10", "1.0", 1) == "Virtual unit read"
    assert checkpoint_action("staged-ot-10", "1.1", 1) == "Virtual unit read"
    assert checkpoint_action("staged-filtration-chain-full", "1.6", 3) == ("Historical checkpoint")
    assert checkpoint_action("staged-filtration-a", "1.5", 6) == "Historical checkpoint"
    assert checkpoint_action("retired-episode", "1.0", 1) == LEGACY
