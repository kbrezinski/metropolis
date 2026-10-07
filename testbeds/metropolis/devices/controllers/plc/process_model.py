"""The water-process behaviour behind the PLC's holding registers.

This is a small coupled model, not a hydraulic simulation. Its purpose is to
make the registers depend on each other and on the actuator coils, so telemetry
is not static: a detector can no longer treat "a value moved" as the signal.

The registers hold tenths of an engineering unit, matching the documented map:

* 0 process level, percent x 10
* 1 flow rate, units per second x 10
* 2 water quality, percent x 10
* 3 heartbeat counter, incremented elsewhere

Everything here is expressed in those tenths, so a level of ``650`` means 65.0
percent. ``ProcessState`` keeps fractions of a tenth, because one tick moves the
level by less than a tenth: rounding every tick would stall the process at its
starting value. Only ``registers()`` rounds, which is the point at which the
value has to fit an integer register.

Inflow follows the pump coil and outflow follows the level, so the level settles
near a baseline instead of draining or overflowing: a fuller tank drains faster,
and the pump pushes back as it empties. Writing a register from outside moves the
process and the model steers it back, the way a real plant responds to a
manipulated value rather than accepting it.

Quality drifts down as water is treated and recovers slowly while the pump runs,
which keeps it moving without needing a separate dosing model.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bounds, in tenths, keeping every value inside the documented register range.
LEVEL_MIN = 500
LEVEL_MAX = 1000
FLOW_MIN = 0
FLOW_MAX = 2000
QUALITY_MIN = 8500
QUALITY_MAX = 10000

# Inflow follows the pump, in tenths. Both values sit either side of the
# documented example flow of 12.0 units/s, so the register stays in a plausible
# band while still tracking the actuator.
FLOW_PUMP_ON = 240.0
FLOW_PUMP_OFF = 120.0
# Outflow rises with level so the tank self-limits. The equilibrium is 65.0
# percent with the pump off and 80.0 with it on.
OUTFLOW_BASE = 110.0
OUTFLOW_PER_LEVEL = 0.02


@dataclass(frozen=True)
class ProcessState:
    """One sample of the modelled process, in tenths, possibly fractional."""

    level: float
    flow: float
    quality: float

    def registers(self) -> list[int]:
        """The three process registers, rounded to the integers they must be."""
        return [
            int(round(self.level)),
            int(round(self.flow)),
            int(round(self.quality)),
        ]


def update(state: ProcessState, seconds: float, pump_on: bool) -> ProcessState:
    """Advance the process by ``seconds`` with the pump in its current state.

    Pure: the same state, interval, and pump state always produce the same
    result, so the model can be tested without a server or a clock.
    """
    inflow = FLOW_PUMP_ON if pump_on else FLOW_PUMP_OFF
    outflow = OUTFLOW_BASE + OUTFLOW_PER_LEVEL * state.level
    level = state.level + (inflow - outflow) * seconds

    # The flow register reports the inflow the pump is producing, so it agrees
    # with how the level is actually moving.
    flow = inflow

    quality = state.quality + (2.0 if pump_on else -1.0) * seconds

    return ProcessState(
        level=min(LEVEL_MAX, max(LEVEL_MIN, level)),
        flow=min(FLOW_MAX, max(FLOW_MIN, flow)),
        quality=min(QUALITY_MAX, max(QUALITY_MIN, quality)),
    )


def initial(level: int, flow: int, quality: int) -> ProcessState:
    """A starting state from register values."""
    return ProcessState(level=float(level), flow=float(flow), quality=float(quality))
