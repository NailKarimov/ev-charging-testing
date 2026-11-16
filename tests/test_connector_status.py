"""The pilot wire and what the station reports because of it.

Between the station and the car runs a single wire called Control Pilot (IEC 61851-1). The car changes
its voltage to say what it wants: state A nothing is plugged in, B the cable is in, C the car is ready
to take power. The station turns that, plus what it is willing to give, into the one word it reports to
the back office. Getting that word wrong is what makes an app say "charging" to a driver whose car is
not charging.
"""

from __future__ import annotations

import pytest

from evse_lab.station import MIN_CHARGE_A, Pilot, Status
from support.evidence import step
from support.rig import Rig


def status(rig: Rig, connector: int = 1) -> Status:
    return rig.station.connector(connector).status


@pytest.mark.smoke
def test_the_pilot_states_walk_through_a_session_in_order(booted: Rig) -> None:
    """Over one session the pilot goes A, B, C, B, A, and the reported status follows each step."""
    seen = []
    with step("Nothing is plugged in"):
        seen.append((booted.station.connector(1).pilot, status(booted)))
    with step("The cable goes into the car"):
        booted.plug_in()
        seen.append((booted.station.connector(1).pilot, status(booted)))
    with step("The card is accepted and the car says it is ready"):
        booted.card("CARD-OK")
        booted.car_ready()
        seen.append((booted.station.connector(1).pilot, status(booted)))
    with step("The driver stops the session"):
        booted.card("CARD-OK")
        seen.append((booted.station.connector(1).pilot, status(booted)))
    with step("The cable comes out"):
        booted.unplug()
        seen.append((booted.station.connector(1).pilot, status(booted)))

    assert [p for p, _ in seen] == [Pilot.A, Pilot.B, Pilot.C, Pilot.C, Pilot.A]
    assert [s for _, s in seen] == [
        Status.AVAILABLE,
        Status.PREPARING,
        Status.CHARGING,
        Status.FINISHING,
        Status.AVAILABLE,
    ]


def test_a_cable_with_nobody_authorised_is_preparing_not_charging(booted: Rig) -> None:
    """A cable plugged in with no card presented is reported as preparing, never as charging."""
    booted.plug_in()
    booted.car_ready()
    booted.charge(60)
    assert status(booted) is Status.PREPARING
    assert booted.station.power_w(1) == 0.0


def test_the_station_says_who_is_holding_back(booted: Rig) -> None:
    """When nothing is flowing the station says whether the car or the station itself is the reason."""
    booted.start_session()
    booted.charge(60)
    assert status(booted) is Status.CHARGING
    with step("The car stops asking for power"):
        booted.car_ready(False)
    assert status(booted) is Status.SUSPENDED_EV, "the car is the reason"
    with step("The car is ready again, but the operator allows less than 6 A"):
        booted.car_ready(True)
        booted.profile(MIN_CHARGE_A - 1)
        booted.charge(5)
    assert status(booted) is Status.SUSPENDED_EVSE, "the station is the reason"


def test_a_fault_on_the_pilot_is_reported_at_once(booted: Rig) -> None:
    """A fault on the pilot wire is reported as a fault, and no power flows."""
    booted.start_session()
    booted.charge(60)
    with step("The pilot wire reports a fault"):
        booted.station.connector(1).pilot = Pilot.E
        booted.station.advance(1)
        booted.talk.settle()
    assert status(booted) is Status.FAULTED
    assert booted.station.power_w(1) == 0.0


def test_every_change_of_status_is_reported_once(booted: Rig) -> None:
    """The station reports a change of status when it happens, and does not repeat itself in between."""
    booted.start_session()
    booted.charge(600, step=60)
    reported = [s for s in booted.csms.statuses]
    changes = [r for r in reported if _name(r) in ("Charging", "Occupied")]
    assert len(changes) == 1, "ten minutes of charging is one change of status, not ten"


def test_the_two_connectors_report_their_own_status(booted: Rig) -> None:
    """Each connector reports its own status, and the number it reports is its own."""
    booted.plug_in(1)
    booted.start_session("CARD-OK-2", connector=2)
    booted.charge(60)
    assert status(booted, 1) is Status.PREPARING
    assert status(booted, 2) is Status.CHARGING
    numbers = {_connector(s) for s in booted.csms.statuses}
    assert numbers == {1, 2}


def test_a_connector_out_of_service_comes_back_when_it_is_put_back(booted: Rig) -> None:
    """A connector taken out of service reports itself unavailable and free again when it is restored."""
    if booted.version == "1.6":
        booted.order("ChangeAvailability", {"connectorId": 1, "type": "Inoperative"})
        assert status(booted) is Status.UNAVAILABLE
        booted.order("ChangeAvailability", {"connectorId": 1, "type": "Operative"})
    else:
        booted.order("ChangeAvailability", {"operationalStatus": "Inoperative", "evse": {"id": 1, "connectorId": 1}})
        assert status(booted) is Status.UNAVAILABLE
        booted.order("ChangeAvailability", {"operationalStatus": "Operative", "evse": {"id": 1, "connectorId": 1}})
    assert status(booted) is Status.AVAILABLE


def _name(payload: dict[str, object]) -> str:
    return str(payload.get("status") or payload.get("connectorStatus"))


def _connector(payload: dict[str, object]) -> int:
    """OCPP 1.6 numbers connectors, 2.0.1 numbers EVSEs; both are plain whole numbers."""
    number = payload["connectorId"] if "errorCode" in payload else payload.get("evseId", 0)
    assert isinstance(number, int)
    return number
