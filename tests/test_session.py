"""A charging session from the cable going in to the cable coming out.

The same tests run on OCPP 1.6 and on 2.0.1. What the driver does is identical; only the messages
differ, and the test says so in the words of whichever version is running.
"""

from __future__ import annotations

import pytest

from evse_lab.schemas import V16, V201
from evse_lab.station import NOMINAL_V, Reason, Status
from support.evidence import step
from support.rig import Rig


@pytest.mark.smoke
def test_the_station_registers_itself_when_it_powers_on(rig: Rig) -> None:
    """When it powers on the station reports who it is, and the back office accepts it and sets the heartbeat."""
    with step("Power on"):
        rig.boot()
    boot = rig.cp.last("BootNotification")
    assert boot.ok
    assert boot.payload["status"] == "Accepted"
    assert boot.payload["interval"] == 300
    assert rig.csms.boots == 1
    name = boot.call.payload
    reported = name.get("chargingStation", name)
    assert reported.get("chargePointModel", reported.get("model")) == "SIM-AC-22"
    assert rig.station.booted


@pytest.mark.parametrize("verdict", ["Rejected", "Pending"])
def test_a_station_the_back_office_did_not_accept_does_not_start_working(rig: Rig, verdict: str) -> None:
    """A station the back office refused or left pending does not start reporting: it waits and asks again."""
    rig.csms.boot_status = verdict
    with step(f"The back office answers the registration with {verdict}"):
        rig.boot()
    assert rig.cp.last("BootNotification").payload["status"] == verdict
    assert not rig.station.booted
    with step("An hour passes"):
        rig.charge(3600, step=600)
    assert rig.csms.heartbeats == 0, "a station that was not accepted must not chatter"
    assert rig.csms.statuses == [], "and must not report its connectors either"


def test_a_fresh_station_says_every_connector_is_free(booted: Rig) -> None:
    """Right after it registers, the station reports each of its connectors as free."""
    first = [s for s in booted.csms.statuses]
    assert len(first) >= 2
    reported = {_status(s) for s in first}
    assert reported == {"Available"}


@pytest.mark.smoke
def test_a_whole_session_is_reported_from_start_to_finish(booted: Rig) -> None:
    """A full session reaches the back office: who charged, how long, and how much energy was delivered."""
    with step("The driver plugs the cable into the car"):
        booted.plug_in()
    with step("The driver holds the card to the reader"):
        assert booted.card("CARD-OK").value == "Accepted"
    with step("The car says it is ready and charging begins"):
        booted.car_ready()
    with step("Half an hour of charging"):
        booted.charge(1800, step=300)
    with step("The driver holds the same card again to stop"):
        booted.card("CARD-OK")
    with step("The cable comes out"):
        booted.unplug()
        booted.charge(2)

    session = booted.csms.session_of("CARD-OK")
    assert not session.running
    assert session.connector == 1
    assert session.reason == "Local"
    # 16 A at 230 V for 1800 s is 1840 Wh; the meter counts whole watt-hours.
    assert session.energy_wh == pytest.approx(1840, abs=2)
    assert len(session.samples) == 30  # a reading a minute
    assert session.samples == sorted(session.samples), "the meter only goes up"


def test_the_station_tells_the_back_office_every_step_of_the_session(booted: Rig) -> None:
    """The connector is reported free, then preparing, then charging, then finishing, then free again."""
    booted.start_session()
    booted.charge(120)
    booted.card("CARD-OK")
    booted.unplug()
    booted.charge(2)
    seen = [_status(s) for s in booted.csms.statuses]
    if booted.version == V16:
        assert seen == ["Available", "Available", "Preparing", "SuspendedEV", "Charging", "Finishing", "Available"]
    else:
        # 2.0.1 has a shorter list: everything with a cable in it is "Occupied", so the steps in
        # between are not reported again. See docs/test-report.md, finding EVC-3.
        assert seen == ["Available", "Available", "Occupied", "Available"]


def test_the_energy_the_back_office_bills_is_the_energy_the_meter_counted(booted: Rig) -> None:
    """What the back office will bill equals the difference the station's own meter shows, to the watt-hour."""
    booted.start_session()
    before = int(booted.station.meter_wh)
    booted.charge(600, step=120)
    booted.card("CARD-OK")
    booted.charge(2)
    after = int(booted.station.meter_wh)
    session = booted.csms.session_of("CARD-OK")
    assert session.energy_wh == after - before
    assert session.meter_start == before


def test_power_is_the_current_the_car_takes_at_the_mains_voltage(booted: Rig) -> None:
    """While charging, the power reported equals the current the car takes times 230 V."""
    booted.start_session()
    booted.charge(90)
    amps = booted.station.connector(1).drawn_a(booted.station.clock)
    assert amps == 16.0  # the car asks for 16 A and the station allows 32 A
    assert booted.station.power_w(1) == pytest.approx(NOMINAL_V * 16.0)


def test_a_car_that_is_not_ready_pauses_the_session_without_ending_it(booted: Rig) -> None:
    """When the car stops taking power the session stays open and the station reports the car as the cause."""
    booted.start_session()
    booted.charge(120)
    with step("The car stops taking power"):
        booted.car_ready(False)
        booted.charge(60)
    assert booted.station.connector(1).status is Status.SUSPENDED_EV
    assert booted.station.connector(1).transaction is not None
    assert booted.csms.session_of("CARD-OK").running
    paused = int(booted.station.meter_wh)
    booted.charge(120)
    assert int(booted.station.meter_wh) == paused, "no energy while the car is not taking any"
    with step("The car is ready again"):
        booted.car_ready(True)
        booted.charge(60)
    assert booted.station.connector(1).status is Status.CHARGING
    assert int(booted.station.meter_wh) > paused


def test_pulling_the_cable_out_ends_the_session_and_says_why(booted: Rig) -> None:
    """If the cable is pulled out while charging, the session ends and the reason given is the car leaving."""
    booted.start_session()
    booted.charge(300)
    with step("The cable is pulled out of the car"):
        booted.unplug()
        booted.charge(2)
    session = booted.csms.session_of("CARD-OK")
    assert not session.running
    expected = "EVDisconnected"
    assert session.reason == expected
    assert booted.station.connector(1).status is Status.AVAILABLE


def test_two_cars_charge_on_two_connectors_at_once(booted: Rig) -> None:
    """Two drivers on the two connectors get two separate sessions, each with its own energy."""
    with step("The first car starts on connector 1"):
        booted.start_session("CARD-OK", connector=1)
    booted.charge(600, step=300)
    with step("A second car starts on connector 2 while the first is charging"):
        booted.start_session("CARD-OK-2", connector=2)
    booted.charge(600, step=300)
    booted.card("CARD-OK", 1)
    booted.card("CARD-OK-2", 2)
    booted.charge(2)

    first = booted.csms.session_of("CARD-OK")
    second = booted.csms.session_of("CARD-OK-2")
    assert first.key != second.key
    assert {first.connector, second.connector} == {1, 2}
    assert first.energy_wh > second.energy_wh, "the first car charged for longer"
    assert len(booted.csms.sessions) == 2


def test_the_station_keeps_in_touch_while_nothing_happens(booted: Rig) -> None:
    """An idle station sends a heartbeat at the interval the back office asked for, and no more often."""
    booted.charge(1200, step=60)
    assert booted.csms.heartbeats == 4  # 1200 s at the 300 s interval


def test_the_back_office_can_change_how_often_the_station_calls_in(booted: Rig) -> None:
    """After the back office shortens the heartbeat interval, the station calls in that much more often."""
    if booted.version == V16:
        answer = booted.order("ChangeConfiguration", {"key": "HeartbeatInterval", "value": "60"})
    else:
        answer = booted.order(
            "SetVariables",
            {
                "setVariableData": [
                    {
                        "component": {"name": "OCPPCommCtrlr"},
                        "variable": {"name": "HeartbeatInterval"},
                        "attributeValue": "60",
                    }
                ]
            },
        )
    assert _accepted(answer.payload)
    booted.csms.heartbeats = 0
    booted.charge(300, step=60)
    assert booted.csms.heartbeats == 5


@pytest.mark.v16
def test_in_1_6_the_back_office_hands_out_the_transaction_number(booted: Rig) -> None:
    """In OCPP 1.6 the station does not know the transaction number until the back office answers."""
    booted.plug_in()
    booted.card("CARD-OK")
    transaction = booted.station.connector(1).transaction
    assert transaction is not None
    assert transaction.remote_id == 1001
    assert booted.cp.last("StartTransaction").payload["transactionId"] == 1001
    assert "StartTransaction" in booted.cp.sent_actions()


@pytest.mark.v201
def test_in_2_0_1_the_station_chooses_the_transaction_id_itself(booted: Rig) -> None:
    """In OCPP 2.0.1 the station names the transaction, so it can start one with nobody listening."""
    booted.plug_in()
    booted.card("CARD-OK")
    started = booted.cp.last("TransactionEvent")
    assert started.call.payload["eventType"] == "Started"
    assert started.call.payload["transactionInfo"]["transactionId"] == "TX-000001"
    assert started.call.payload["seqNo"] == 0


@pytest.mark.v201
def test_in_2_0_1_every_event_of_a_session_is_numbered_in_order(booted: Rig) -> None:
    """Each message of a session carries the next number, so the back office can see nothing was lost."""
    booted.start_session()
    booted.charge(300, step=60)
    booted.card("CARD-OK")
    booted.charge(2)
    session = booted.csms.session_of("CARD-OK")
    assert session.seq_nos == list(range(len(session.seq_nos)))
    assert len(session.seq_nos) >= 6


def _status(payload: dict[str, object]) -> str:
    return str(payload.get("status") or payload.get("connectorStatus"))


def _accepted(payload: dict[str, object]) -> bool:
    if "status" in payload:
        return payload["status"] == "Accepted"
    results = payload.get("setVariableResult") or payload.get("getVariableResult") or []
    assert isinstance(results, list)
    return all(row.get("attributeStatus") == "Accepted" for row in results)


__all__ = ["V16", "V201", "Reason"]
