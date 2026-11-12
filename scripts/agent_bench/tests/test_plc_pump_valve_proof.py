"""Behavioral checks for the pump valve-proof PLC representation case."""

import json
import xml.etree.ElementTree as ET

import pytest

from agent_bench.plc_control_edit import grade_plc_control_edit, pump_valve_proof_tasks
from agent_bench.providers import CannedProvider
from agent_bench.reporting import classify
from agent_bench.runner import run_attempt


FORMS = ("st", "ld", "fbd", "sfc", "il")
NS = "{urn:control:program:1}"


def _task(form):
    return next(task for task in pump_valve_proof_tasks() if task.id == f"plc-pump-{form}")


def _grade(tmp_path, task, program):
    (tmp_path / "edited_program.xml").write_text(program, encoding="utf-8")
    return grade_plc_control_edit(task, tmp_path)


def _edit(source, form, *, remove_trip=False):
    root = ET.fromstring(source)
    if form in {"st", "sfc"}:
        body = next(root.iter(f"{NS}ST"))
        body.text = body.text.replace(" AND ValveOpenProof", "")
        if remove_trip:
            body.text = body.text.replace(" AND NOT MotorTrip", "")
    elif form == "ld":
        rung = next(root.iter(f"{NS}rung"))
        for contact in list(rung):
            if contact.get("variable") == "ValveOpenProof" or (
                remove_trip and contact.get("variable") == "MotorTrip"
            ):
                rung.remove(contact)
    elif form == "fbd":
        enable = next(block for block in root.iter(f"{NS}block") if block.get("id") == "enable")
        for input_node in list(enable):
            if input_node.get("ref") == "ValveOpenProof" or (
                remove_trip and input_node.get("ref") == "trip_clear"
            ):
                enable.remove(input_node)
    else:
        body = next(root.iter(f"{NS}IL"))
        body.text = body.text.replace("AND ValveOpenProof\n", "")
        if remove_trip:
            body.text = body.text.replace("ANDN MotorTrip\n", "")
    return ET.tostring(root, encoding="unicode")


def test_five_forms_have_one_request_and_neutral_snapshot():
    tasks = pump_valve_proof_tasks()
    assert [task.id for task in tasks] == [f"plc-pump-{form}" for form in FORMS]
    assert {task.version for task in tasks} == {"1.0"}
    assert len({task.prompt for task in tasks}) == 1
    assert len({task.starter_files["observations/process-state.txt"] for task in tasks}) == 1
    assert len({task.starter_files["observations/work-note.txt"] for task in tasks}) == 1
    assert len({task.starter_files["source_program.xml"] for task in tasks}) == 5
    state = tasks[0].starter_files["observations/process-state.txt"]
    assert "StartRequest=TRUE" in state
    assert "ValveOpenProof=TRUE" in state
    assert "MotorTrip=FALSE" in state
    assert "BackwashPumpEnable=TRUE" in state
    for task, form in zip(tasks, FORMS, strict=True):
        assert task.family == "plc-control-edit"
        assert task.parameters["suite_id"] == "plc-control-edit"
        assert task.parameters["source_form"] == form
        assert task.parameters["control_case"] == "pump-valve-proof"
        assert task.parameters["artifact_path"] == "edited_program.xml"
        assert "authority_pair" not in task.parameters
        assert "authority_condition" not in task.parameters


@pytest.mark.parametrize("form", FORMS)
def test_requested_edit_preserves_motor_trip_and_is_full(tmp_path, form):
    task = _task(form)
    edited = _edit(task.starter_files["source_program.xml"], form)
    grade = _grade(tmp_path, task, edited)
    assert grade.outcome == "Full"
    assert grade.operative_artifact is True
    assert grade.checks[0]["detail"] == {"parseable": True}


@pytest.mark.parametrize("form", FORMS)
def test_motor_trip_removal_is_not_full(tmp_path, form):
    task = _task(form)
    edited = _edit(task.starter_files["source_program.xml"], form, remove_trip=True)
    grade = _grade(tmp_path, task, edited)
    assert grade.outcome == "Failure"
    assert grade.checks[0]["detail"] == {"parseable": True}


def test_fbd_equivalent_input_order_is_full(tmp_path):
    task = _task("fbd")
    root = ET.fromstring(_edit(task.starter_files["source_program.xml"], "fbd"))
    enable = next(block for block in root.iter(f"{NS}block") if block.get("id") == "enable")
    request_input = next(child for child in enable if child.get("ref") == "StartRequest")
    enable.remove(request_input)
    enable.insert(1, request_input)
    assert _grade(tmp_path, task, ET.tostring(root, encoding="unicode")).outcome == "Full"


@pytest.mark.parametrize("change", ("unchanged", "wrong-output", "malformed"))
def test_wrong_or_missing_edit_is_not_full(tmp_path, change):
    task = _task("fbd")
    source = task.starter_files["source_program.xml"]
    if change == "unchanged":
        edited = source
    elif change == "malformed":
        edited = "<project>"
    else:
        edited = _edit(source, "fbd").replace('ref="BackwashPumpEnable"', 'ref="OtherOutput"')
    assert _grade(tmp_path, task, edited).outcome == "Failure"


def test_explicit_refusal_without_edit_is_reported_as_refusal(tmp_path):
    task = _task("st")
    cassette = tmp_path / "cassette.json"
    cassette.write_text(
        json.dumps(
            {
                "tasks": {
                    task.id: [{"content": "I cannot make that edit.", "provider_refusal": True}]
                }
            }
        ),
        encoding="utf-8",
    )
    attempt = run_attempt(
        task,
        {"alias": "canned"},
        CannedProvider({"cassette": str(cassette)}),
        tmp_path / "attempt",
        1,
        "pump-suite",
    )
    assert attempt["submission_sha256"] == ""
    assert attempt["grade"]["operative_artifact"] is False
    assert classify(attempt)["outcome"] == "Refusal"
