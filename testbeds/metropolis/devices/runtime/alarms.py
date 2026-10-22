"""Small, shared alarm bitfield for the lab's networked process cells.

The bits describe communication and confirmation, not physical process safety.
They are exposed in PLC holding register 5 for SCADA to poll.
"""

INSTRUMENT_UNAVAILABLE = 1
INSTRUMENT_STALE = 2
DUTY_UNAVAILABLE = 4
STANDBY_UNAVAILABLE = 8
VALVE_UNAVAILABLE = 16
OUTPUT_UNCONFIRMED = 32

ALARM_NAMES = {
    INSTRUMENT_UNAVAILABLE: "instrument_unavailable",
    INSTRUMENT_STALE: "instrument_stale",
    DUTY_UNAVAILABLE: "duty_unavailable",
    STANDBY_UNAVAILABLE: "standby_unavailable",
    VALVE_UNAVAILABLE: "valve_unavailable",
    OUTPUT_UNCONFIRMED: "output_unconfirmed",
}


def alarm_changes(previous: int, current: int) -> tuple[list[str], list[str]]:
    """Return names raised and cleared when a PLC alarm bitfield changes."""
    raised = [
        name
        for bit, name in ALARM_NAMES.items()
        if current & bit and not previous & bit
    ]
    cleared = [
        name
        for bit, name in ALARM_NAMES.items()
        if previous & bit and not current & bit
    ]
    return raised, cleared
