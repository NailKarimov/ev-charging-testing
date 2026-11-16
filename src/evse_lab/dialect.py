"""What is common to both OCPP versions: the clock, the shape of a meter reading, and the interface.

A ``Dialect`` turns the station's events into the calls of one OCPP version and turns the calls of the
back office back into orders for the station. Everything that differs between 1.6 and 2.0.1 lives in
``v16.py`` and ``v201.py``; the station and the tests of its behaviour do not change with the version.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from evse_lab.rpc import Call
from evse_lab.station import ChargePoint, Event

# The simulated day. A fixed date keeps every run and every recorded message identical.
EPOCH = datetime(2026, 6, 15, tzinfo=UTC)


def iso(clock: int) -> str:
    """A point on the simulated clock as the timestamp OCPP wants."""
    return (EPOCH + timedelta(seconds=clock)).isoformat().replace("+00:00", "Z")


def clock_of(timestamp: str) -> int:
    """The inverse of ``iso``: seconds on the simulated clock."""
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return int((moment - EPOCH).total_seconds())


# Energy so far, and the power and current at this moment.
MEASURANDS = (
    ("Energy.Active.Import.Register", "Wh"),
    ("Power.Active.Import", "W"),
    ("Current.Import", "A"),
)


def sampled_value(version: str, wh: int, watts: float, amps: float) -> list[dict[str, Any]]:
    """One meter reading, in the shape its version demands.

    The two versions disagree, and the official schemas are strict about it: 1.6 carries the number as
    text with a plain ``unit`` field, while 2.0.1 carries a real number and puts the unit inside
    ``unitOfMeasure``. Writing one shape for both is a mistake the schema check catches at once.
    """
    numbers = (wh, round(watts, 1), round(amps, 1))
    out: list[dict[str, Any]] = []
    for (measurand, unit), number in zip(MEASURANDS, numbers, strict=True):
        if version == "1.6":
            out.append({"value": f"{number}", "measurand": measurand, "unit": unit, "context": "Sample.Periodic"})
        else:
            out.append(
                {
                    "value": float(number),
                    "measurand": measurand,
                    "context": "Sample.Periodic",
                    "unitOfMeasure": {"unit": unit},
                }
            )
    return out


class Dialect(Protocol):
    """One version of OCPP."""

    version: str
    name: str

    def calls_for(self, station: ChargePoint, event: Event) -> list[Call]:
        """The messages this event becomes. Usually one; a boot answer may bring several."""
        ...

    def on_result(self, station: ChargePoint, call: Call, payload: dict[str, Any]) -> None:
        """What the station does with the answer to a message it sent."""
        ...

    def handle(self, station: ChargePoint, call: Call) -> dict[str, Any]:
        """Carry out an order from the back office and give the answer payload."""
        ...

    def supported(self) -> frozenset[str]:
        """Orders this station accepts. Anything else is answered with NotImplemented."""
        ...
