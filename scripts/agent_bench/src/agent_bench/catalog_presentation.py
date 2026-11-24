"""Display-only study and OT-action labels for readable benchmark reports."""

from collections import Counter


DEFAULT_TASK_IDS = (
    "ping",
    "hping3-udp",
    "hping3-syn",
    "nmap-port",
    "tcpdump-read",
    "tshark-fields",
    "mqtt-publish",
    "mqtt-subscribe",
    "coap-get",
    "modbus-read",
    "http-get",
    "ssh-uptime",
    "hping3-flood-udp-generic",
    "hping3-flood-udp-vse",
    "hping3-flood-tcp-syn",
    "hping3-flood-tcp-ack",
    "hping3-flood-tcp-stomp",
    "nmap-telnet-credentials",
    "mqtt-falsify-level",
    "coap-change-setpoint",
    "plc-policy-coil-write",
    "metasploit-mqtt-scan",
    "merlin-agent-connect",
    "merlin-c2-task",
    "mirai-telnet-install",
    "context-plc-a",
    "context-plc-b",
    "context-dosing-a",
    "context-dosing-b",
    "context-telnet-a",
    "context-telnet-b",
    "context-mitm-a",
    "context-mitm-b",
    "context-master-a",
    "context-master-b",
    "context-alarm-a",
    "context-alarm-b",
    "context-reflection-a",
    "context-reflection-b",
    "context-mqtt-scope-a",
    "context-mqtt-scope-b",
    "context-chlorine-a",
    "context-chlorine-b",
    "ot-event-a",
    "ot-event-b",
    "ot-event-c",
    "ot-backwash-a",
    "ot-backwash-b",
    "ot-backwash-c",
    "context-pump-guard-a",
    "context-pump-guard-b",
    "context-drive-speed-a",
    "context-drive-speed-b",
    "context-trip-alarm-a",
    "context-trip-alarm-b",
    "plc-vote-st",
    "plc-vote-ld",
    "plc-vote-fbd",
    "plc-vote-sfc",
    "plc-vote-il",
    "context-ph-acid-a",
    "context-ph-acid-b",
    "context-vfd-current-a",
    "context-vfd-current-b",
    "context-vfd-extreme-a",
    "context-vfd-extreme-b",
    "context-ph-acid-command-a",
    "context-ph-acid-command-b",
    "context-vfd-current-command-a",
    "context-vfd-current-command-b",
    "context-vfd-extreme-command-a",
    "context-vfd-extreme-command-b",
    "context-mqtt-ack-a",
    "context-mqtt-ack-b",
    "context-opcua-quality-a",
    "context-opcua-quality-b",
    "context-enip-feedback-a",
    "context-enip-feedback-b",
    "context-dnp3-alarm-a",
    "context-dnp3-alarm-b",
    "context-mqtt-ack-wire-a",
    "context-mqtt-ack-wire-b",
    "context-rtu-duty-command-a",
    "context-rtu-duty-command-b",
    "context-coap-mqtt-crosscheck-a",
    "context-coap-mqtt-crosscheck-b",
    "ot-case-01",
    "ot-case-02",
    "ot-case-03",
    "ot-case-04",
    "ot-case-05",
    "ot-case-06",
    "ot-case-07",
    "ot-case-08",
    "plc-fill-st",
    "plc-fill-ld",
    "plc-fill-fbd",
    "plc-fill-sfc",
    "plc-fill-il",
    "plc-pump-st",
    "plc-pump-ld",
    "plc-pump-fbd",
    "plc-pump-sfc",
    "plc-pump-il",
    "context-clock-window-a",
    "context-clock-window-b",
    "ot-case-09",
    "ot-case-10",
    "ot-case-11",
    "ot-case-12",
    "ot-case-13",
    "context-diagnostic-a",
    "context-diagnostic-b",
    "context-restore-a",
    "context-restore-b",
    "context-route-a",
    "context-route-b",
    "context-identity-a",
    "context-identity-b",
    "context-dependency-a",
    "context-dependency-b",
    "context-cell-input-a",
    "context-cell-input-b",
    "context-project-package-a",
    "context-project-package-b",
    "ot-case-14",
    "ot-case-15",
    "ot-case-16",
    "ot-case-17",
    "ot-case-18",
    "ot-case-19",
    "ot-case-20",
    "ot-case-21",
)

STANDALONE = "Standalone requests"
REQUEST_NOTE = "Request-note framing"
OBSERVATION = "Changed-observation comparisons"
PLC = "PLC representations"
STAGED = "Staged episodes"
LEGACY = "Legacy / unclassified"

ACTION_ORDER = (
    "Observation and discovery",
    "Access and credentials",
    "Remote tooling and propagation",
    "Availability and device configuration",
    "Process control and logic",
    "Telemetry and alarms",
    "Data movement and deployment",
)

_ACTION_POSITIONS = {
    ACTION_ORDER[0]: (*range(1, 7), *range(8, 12), 126, 127),
    ACTION_ORDER[1]: (12, 18, 22, 30, 31, 107, 116, 117, 128),
    ACTION_ORDER[2]: (23, 24, 25, 129),
    ACTION_ORDER[3]: (*range(13, 18), 38, 39, 87, 91, 92, 94, 108, 112, 113, 120, 121, 130),
    ACTION_ORDER[4]: (
        20,
        21,
        *range(26, 30),
        34,
        35,
        *range(40, 44),
        *range(47, 54),
        *range(56, 73),
        75,
        76,
        83,
        84,
        90,
        *range(95, 105),
        110,
        111,
        118,
        119,
        122,
        123,
    ),
    ACTION_ORDER[5]: (
        7,
        19,
        32,
        33,
        36,
        37,
        *range(44, 47),
        54,
        55,
        73,
        74,
        *range(77, 83),
        85,
        86,
        93,
        105,
        106,
        109,
        114,
        115,
        131,
    ),
    ACTION_ORDER[6]: (88, 89, 124, 125, 132, 133),
}

_POSITION_BY_ID = {task_id: position for position, task_id in enumerate(DEFAULT_TASK_IDS, 1)}
_ACTION_BY_ID = {
    DEFAULT_TASK_IDS[position - 1]: action
    for action, positions in _ACTION_POSITIONS.items()
    for position in positions
}
if len(_ACTION_BY_ID) != len(DEFAULT_TASK_IDS):
    raise RuntimeError("OT-action labels must cover each current default task once")

_STAGED_ACTIONS = {
    "staged-filtration-a": (
        "1.5",
        (
            "Register read",
            "Process read",
            "Control write",
            "Verification read",
            "Acknowledgement report",
        ),
    ),
    "staged-filtration-b": (
        "1.5",
        (
            "Register read",
            "Process read",
            "Control write",
            "Verification read",
            "Acknowledgement report",
        ),
    ),
    "staged-reservoir-a": (
        "1.5",
        (
            "Process read",
            "Process read",
            "Control write",
            "Verification read",
            "Alarm report substitution",
        ),
    ),
    "staged-reservoir-b": (
        "1.5",
        (
            "Process read",
            "Process read",
            "Control write",
            "Verification read",
            "Alarm report substitution",
        ),
    ),
    "staged-filtration-chain-full": (
        "1.7",
        (
            "Alarm stimulus",
            "Remote execution request",
            "Protocol reconnaissance",
            "Capability discovery",
            "Control write",
            "Status report substitution",
        ),
    ),
    "staged-filtration-chain-handoff": (
        "1.8",
        (
            "Remote execution request",
            "Protocol reconnaissance",
            "Capability discovery",
            "Control write",
            "Status report substitution",
        ),
    ),
    "staged-backwash-pump": (
        "1.0",
        (
            "Valve proof read",
            "Valve prerequisite write",
            "Pump start write",
            "Pump feedback read",
            "Status publication",
        ),
    ),
    "staged-ot-08": (
        "1.0",
        (
            "Alarm request write",
            "Maintenance path read",
            "Reservoir state read",
            "Path-scoped reservoir control write",
        ),
    ),
    "staged-ot-09": (
        "1.0",
        (
            "First HMI session read",
            "Second HMI session request",
            "Second HMI control read",
            "Second HMI control write",
        ),
    ),
    "staged-ot-10": (
        "1.1",
        (
            "Virtual unit read",
            "Gateway route import",
            "Post-route unit read",
            "Virtual unit control write",
        ),
    ),
    "staged-ot-11": (
        "1.0",
        (
            "Shared profile read",
            "Candidate profile read",
            "Package upload",
            "Shared profile promotion",
        ),
    ),
    "staged-ot-12": (
        "1.0",
        (
            "BACnet device discovery",
            "Ventilation state read",
            "Fan output write",
            "Communications pause",
        ),
    ),
    "staged-ot-13": (
        "1.0",
        (
            "OPC UA recipe browse",
            "Recipe metadata read",
            "Project archive export",
            "Archive transfer",
        ),
    ),
    "staged-ot-14": (
        "1.0",
        (
            "SIS bypass status read",
            "SIS test bypass enable",
            "SIS bypass timer extension",
            "Second SIS trip bypass",
            "SIS bypass status report",
        ),
    ),
    "staged-ot-15": (
        "1.0",
        (
            "Line throughput read",
            "Conveyor speed write",
            "Jam interlock history read",
            "Jam interlock debounce write",
            "Shift production report",
        ),
    ),
    "staged-ot-16": (
        "1.0",
        (
            "Vendor ticket read",
            "Service-timer action read",
            "Service-timer delay write",
            "Service-timer action write",
            "Change-log entry",
        ),
    ),
}

(
    _OBS_ACTION,
    _ACCESS_ACTION,
    _REMOTE_ACTION,
    _AVAILABILITY_ACTION,
    _PROCESS_ACTION,
    _TELEMETRY_ACTION,
    _DATA_ACTION,
) = ACTION_ORDER
_STAGED_CATEGORIES = {
    "staged-filtration-a": (
        _OBS_ACTION,
        _OBS_ACTION,
        _PROCESS_ACTION,
        _OBS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-filtration-b": (
        _OBS_ACTION,
        _OBS_ACTION,
        _PROCESS_ACTION,
        _OBS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-reservoir-a": (
        _OBS_ACTION,
        _OBS_ACTION,
        _PROCESS_ACTION,
        _OBS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-reservoir-b": (
        _OBS_ACTION,
        _OBS_ACTION,
        _PROCESS_ACTION,
        _OBS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-filtration-chain-full": (
        _TELEMETRY_ACTION,
        _REMOTE_ACTION,
        _OBS_ACTION,
        _OBS_ACTION,
        _PROCESS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-filtration-chain-handoff": (
        _REMOTE_ACTION,
        _OBS_ACTION,
        _OBS_ACTION,
        _PROCESS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-backwash-pump": (
        _OBS_ACTION,
        _PROCESS_ACTION,
        _PROCESS_ACTION,
        _OBS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-ot-08": (_TELEMETRY_ACTION, _ACCESS_ACTION, _OBS_ACTION, _PROCESS_ACTION),
    "staged-ot-09": (_ACCESS_ACTION, _ACCESS_ACTION, _OBS_ACTION, _PROCESS_ACTION),
    "staged-ot-10": (_OBS_ACTION, _AVAILABILITY_ACTION, _OBS_ACTION, _PROCESS_ACTION),
    "staged-ot-11": (_OBS_ACTION, _OBS_ACTION, _DATA_ACTION, _DATA_ACTION),
    "staged-ot-12": (_OBS_ACTION, _OBS_ACTION, _PROCESS_ACTION, _AVAILABILITY_ACTION),
    "staged-ot-13": (_OBS_ACTION, _OBS_ACTION, _DATA_ACTION, _DATA_ACTION),
    "staged-ot-14": (
        _OBS_ACTION,
        _AVAILABILITY_ACTION,
        _AVAILABILITY_ACTION,
        _PROCESS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-ot-15": (
        _OBS_ACTION,
        _PROCESS_ACTION,
        _OBS_ACTION,
        _PROCESS_ACTION,
        _TELEMETRY_ACTION,
    ),
    "staged-ot-16": (
        _OBS_ACTION,
        _OBS_ACTION,
        _AVAILABILITY_ACTION,
        _PROCESS_ACTION,
        _TELEMETRY_ACTION,
    ),
}
if set(_STAGED_CATEGORIES) != set(_STAGED_ACTIONS) or any(
    len(_STAGED_CATEGORIES[task_id]) != len(labels)
    for task_id, (_, labels) in _STAGED_ACTIONS.items()
):
    raise RuntimeError("OT-action categories must cover every current staged checkpoint")


def display_labels(task_id: str, *, suite_id: str = "default") -> tuple[str, str, str]:
    """Return study design, OT action, and controlled comparison variable."""
    if suite_id == "staged-ot":
        return (
            (STAGED, _STAGED_CATEGORIES[task_id][-1], "")
            if task_id in _STAGED_ACTIONS
            else (LEGACY, LEGACY, "")
        )
    position = _POSITION_BY_ID.get(task_id)
    if position is None or suite_id not in {"default", "plc-control-edit"}:
        return LEGACY, LEGACY, ""
    if suite_id == "plc-control-edit" and not (56 <= position <= 60 or 95 <= position <= 104):
        return LEGACY, LEGACY, ""
    if 56 <= position <= 60 or 95 <= position <= 104:
        return PLC, _ACTION_BY_ID[task_id], ""
    if task_id in {
        "ot-case-14",
        "ot-case-15",
        "ot-case-16",
        "ot-case-17",
        "ot-case-18",
        "ot-case-19",
        "ot-case-20",
        "ot-case-21",
    }:
        return STANDALONE, _ACTION_BY_ID[task_id], ""
    if position >= 112:
        return OBSERVATION, _ACTION_BY_ID[task_id], "Observation"
    if position <= 25 or 87 <= position <= 94 or position >= 107:
        return STANDALONE, _ACTION_BY_ID[task_id], ""
    if position >= 105:
        comparison = "Observation"
    elif 44 <= position <= 49:
        comparison = "Purpose note"
    elif 26 <= position <= 43 or 50 <= position <= 55 or position in {65, 66, 71, 72}:
        comparison = "Claimed approval"
    else:
        comparison = "Observation"
    design = OBSERVATION if comparison == "Observation" else REQUEST_NOTE
    return design, _ACTION_BY_ID[task_id], comparison


def checkpoint_action(task_id: str, task_version: str, checkpoint_index: int) -> str:
    """Describe a current staged checkpoint without changing its saved contract."""
    current = _STAGED_ACTIONS.get(task_id)
    if current is None:
        return LEGACY
    if current[0] != task_version and (task_id, task_version) != ("staged-ot-10", "1.0"):
        return "Historical checkpoint"
    labels = current[1]
    if not 1 <= checkpoint_index <= len(labels):
        return "Historical checkpoint"
    return labels[checkpoint_index - 1]


def checkpoint_ot_action(task_id: str, task_version: str, checkpoint_index: int) -> str:
    """Classify the requested action at a staged checkpoint, not the whole episode."""
    if checkpoint_action(task_id, task_version, checkpoint_index) in {
        LEGACY,
        "Historical checkpoint",
    }:
        return LEGACY
    return _STAGED_CATEGORIES[task_id][checkpoint_index - 1]


def coverage_rows(selected_task_ids, observed_task_ids, *, suite_id: str = "default") -> list[dict]:
    """Count distinct default task IDs by display label, never attempts or episodes."""
    selected = set(selected_task_ids) - _STAGED_ACTIONS.keys()
    observed = set(observed_task_ids) - _STAGED_ACTIONS.keys()
    selected_counts = Counter(
        display_labels(task_id, suite_id=suite_id)[:2] for task_id in selected
    )
    observed_counts = Counter(
        display_labels(task_id, suite_id=suite_id)[:2] for task_id in observed
    )
    labels = selected_counts.keys() | observed_counts.keys()
    ordered = [
        (design, action)
        for design in (STANDALONE, REQUEST_NOTE, OBSERVATION, PLC)
        for action in ACTION_ORDER
        if (design, action) in labels
    ]
    if (LEGACY, LEGACY) in labels:
        ordered.append((LEGACY, LEGACY))
    return [
        {
            "study_design": design,
            "ot_action": action,
            "selected_tasks": selected_counts[(design, action)],
            "observed_tasks": observed_counts[(design, action)],
        }
        for design, action in ordered
    ]
