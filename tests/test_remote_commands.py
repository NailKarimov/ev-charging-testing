"""Orders the back office sends to the station, and what the station refuses.

A charge point in the street is operated from a screen somewhere else: start this car, stop that one,
restart, take a connector out of service, release a cable. Each order has to be carried out, refused
with a reason, or ignored safely. A badly handled order either leaves a driver with a locked cable or
lets a stranger stop somebody else's session.
"""

from __future__ import annotations

import pytest

from evse_lab.node import Exchange
from evse_lab.rpc import (
    FORMATION_VIOLATION,
    NOT_IMPLEMENTED,
    NOT_SUPPORTED,
    PROPERTY_CONSTRAINT_VIOLATION,
)
from evse_lab.station import Status
from support.evidence import step
from support.rig import Rig


def _start(rig: Rig, id_tag: str = "CARD-OK", connector: int | None = 1, **extra: object) -> Exchange:
    if rig.version == "1.6":
        payload: dict[str, object] = {"idTag": id_tag, **extra}
        if connector is not None:
            payload["connectorId"] = connector
        return rig.order("RemoteStartTransaction", payload)
    payload = {"idToken": {"idToken": id_tag, "type": "ISO14443"}, "remoteStartId": 1, **extra}
    if connector is not None:
        payload["evseId"] = connector
    return rig.order("RequestStartTransaction", payload)


def _stop(rig: Rig, key: str) -> Exchange:
    if rig.version == "1.6":
        return rig.order("RemoteStopTransaction", {"transactionId": int(key)})
    return rig.order("RequestStopTransaction", {"transactionId": key})


@pytest.mark.smoke
def test_the_back_office_can_start_a_session_from_afar(booted: Rig) -> None:
    """A session started from the back office charges the car just as a card at the reader would."""
    booted.plug_in()
    with step("The operator starts the session from the back office"):
        answer = _start(booted)
    assert answer.payload["status"] == "Accepted"
    booted.car_ready()
    booted.charge(300, step=60)
    booted.charge(2)
    session = booted.csms.session_of("CARD-OK")
    assert session.running
    assert booted.station.connector(1).status is Status.CHARGING


def test_the_back_office_can_stop_the_session_it_started(booted: Rig) -> None:
    """A session stopped from the back office ends with the reason Remote."""
    booted.start_session()
    booted.charge(300, step=60)
    key = booted.transaction_key()
    with step("The operator stops the session"):
        answer = _stop(booted, key)
    assert answer.payload["status"] == "Accepted"
    booted.charge(2)
    session = booted.csms.session_of("CARD-OK")
    assert not session.running
    assert session.reason == "Remote"


def test_stopping_a_session_that_is_not_there_is_refused(booted: Rig) -> None:
    """An order to stop a transaction nobody is running is refused, not silently accepted."""
    booted.start_session()
    answer = _stop(booted, "999999" if booted.version == "1.6" else "TX-999999")
    assert answer.payload["status"] == "Rejected"
    assert booted.csms.session_of("CARD-OK").running, "the real session is untouched"


def test_a_second_start_on_a_busy_connector_is_refused(booted: Rig) -> None:
    """While a car is charging, an order to start another session on that connector is refused."""
    booted.start_session()
    booted.charge(60)
    answer = _start(booted, "CARD-OK-2", connector=1)
    assert answer.payload["status"] == "Rejected"
    assert len(booted.csms.sessions) == 1


def test_a_start_without_a_connector_picks_a_free_one(booted: Rig) -> None:
    """An order that does not say which connector is carried out on a free one."""
    booted.plug_in(1)
    booted.plug_in(2)
    assert _start(booted, "CARD-OK", connector=None).payload["status"] == "Accepted"
    assert _start(booted, "CARD-OK-2", connector=None).payload["status"] == "Accepted"
    assert len(booted.csms.sessions) == 2
    busy = [n for n, c in booted.station.connectors.items() if c.transaction is not None]
    assert sorted(busy) == [1, 2]


def test_an_order_for_a_connector_that_does_not_exist_is_refused(booted: Rig) -> None:
    """An order naming connector 9 on a two-connector station is refused with a reason."""
    answer = _start(booted, connector=9)
    assert answer.payload["status"] == "Rejected"


def test_unlocking_is_refused_while_a_car_is_charging(booted: Rig) -> None:
    """A cable cannot be released in the middle of a session: the order is refused."""
    booted.start_session()
    booted.charge(60)
    with step("The operator tries to release the cable while the car is charging"):
        answer = _unlock(booted, 1)
    assert answer.payload["status"] == "UnlockFailed"
    assert booted.station.connector(1).locked


def test_unlocking_works_once_the_session_is_over(booted: Rig) -> None:
    """After the session ends the cable is released on request."""
    booted.start_session()
    booted.charge(60)
    booted.card("CARD-OK")
    booted.charge(2)
    answer = _unlock(booted, 1)
    assert answer.payload["status"] == "Unlocked"
    assert not booted.station.connector(1).locked


def test_a_connector_taken_out_of_service_refuses_new_sessions(booted: Rig) -> None:
    """A connector the operator took out of service reports itself unavailable and starts nothing."""
    with step("The operator takes connector 1 out of service"):
        answer = _availability(booted, 1, False)
    assert answer.payload["status"] == "Accepted"
    assert booted.station.connector(1).status is Status.UNAVAILABLE
    assert _start(booted, connector=1).payload["status"] == "Rejected"
    assert booted.station.connector(2).status is Status.AVAILABLE, "the other connector keeps working"


def test_taking_a_busy_connector_out_of_service_waits_for_the_car(booted: Rig) -> None:
    """Asked to take a busy connector out of service, the station answers Scheduled and finishes the session."""
    booted.start_session()
    booted.charge(60)
    with step("The operator takes the busy connector out of service"):
        answer = _availability(booted, 1, False)
    assert answer.payload["status"] == "Scheduled"
    assert booted.station.connector(1).status is Status.CHARGING, "the car keeps charging"
    with step("The driver finishes and leaves"):
        booted.card("CARD-OK")
        booted.unplug()
        booted.charge(2)
    assert booted.station.connector(1).status is Status.UNAVAILABLE


def test_a_soft_restart_is_accepted_and_the_station_comes_back(booted: Rig) -> None:
    """A restart is accepted, the station registers itself again, and the back office sees one more boot."""
    boots = booted.csms.boots
    answer = booted.order("Reset", {"type": "Soft" if booted.version == "1.6" else "OnIdle"})
    assert answer.payload["status"] == "Accepted"
    with step("The station restarts and registers itself again"):
        booted.station.do_reset()
        booted.boot()
    assert booted.csms.boots == boots + 1
    assert booted.station.booted


def test_a_hard_restart_ends_a_running_session_and_says_why(booted: Rig) -> None:
    """A restart that cannot wait ends the running session with PowerLoss, so no energy is unaccounted for."""
    booted.start_session()
    booted.charge(300, step=60)
    booted.order("Reset", {"type": "Hard" if booted.version == "1.6" else "Immediate"})
    with step("The station restarts at once"):
        booted.station.do_reset()
        booted.charge(2)
        booted.boot()
    session = booted.csms.session_of("CARD-OK")
    assert not session.running
    assert session.reason == "PowerLoss"
    assert session.energy_wh > 0


def test_the_back_office_can_ask_for_a_message_at_any_time(booted: Rig) -> None:
    """Asked to report its status, the station sends one straight away."""
    before = len(booted.csms.statuses)
    answer = booted.order("TriggerMessage", {"requestedMessage": "StatusNotification"})
    assert answer.payload["status"] == "Accepted"
    assert len(booted.csms.statuses) > before


def test_an_order_the_station_does_not_know_is_refused_as_not_implemented(booted: Rig) -> None:
    """An order that exists in the standard but not on this station is refused with NotImplemented."""
    action = "GetDiagnostics" if booted.version == "1.6" else "GetLog"
    payload = (
        {"location": "ftp://example.org/logs"}
        if booted.version == "1.6"
        else {"logType": "DiagnosticsLog", "requestId": 1, "log": {"remoteLocation": "ftp://example.org/logs"}}
    )
    answer = booted.order(action, payload)
    assert answer.error is not None
    assert answer.error.code == NOT_IMPLEMENTED


def test_an_invented_order_is_refused_as_not_supported(loose: Rig) -> None:
    """An order that is not in the standard at all is refused with NotSupported.

    The back office here does not check what it sends, so the invented order really reaches the wire.
    """
    loose.boot()
    answer = loose.order("MakeCoffee", {})
    assert answer.error is not None
    assert answer.error.code == NOT_SUPPORTED


def test_a_refused_order_leaves_the_station_working(loose: Rig) -> None:
    """After an order it had to refuse, the station answers the next one as usual."""
    loose.boot()
    loose.order("MakeCoffee", {})
    assert _availability(loose, 1, True).payload["status"] == "Accepted"


def test_an_order_with_a_missing_field_is_refused_before_anything_happens(loose: Rig) -> None:
    """An order whose required field is missing is refused against the official schema and changes nothing."""
    loose.boot()
    loose.start_session()
    loose.charge(60)
    answer = loose.order("Reset", {})  # "type" is required by the schema of both versions
    assert answer.error is not None
    assert answer.error.code == FORMATION_VIOLATION
    assert loose.station.connector(1).transaction is not None, "the session is untouched"
    assert loose.station.reset_pending is None, "nothing was carried out"


def test_an_order_with_a_value_outside_the_list_is_refused(loose: Rig) -> None:
    """A restart of a kind the standard does not define is refused and the station does not restart."""
    loose.boot()
    answer = loose.order("Reset", {"type": "Melt"})
    assert answer.error is not None
    assert answer.error.code == FORMATION_VIOLATION
    assert loose.station.reset_pending is None


def test_an_order_for_a_connector_that_is_not_there_is_refused_with_the_right_reason(booted: Rig) -> None:
    """An order naming a connector the station does not have is refused as a property constraint violation.

    The code matters: it tells the back office that the order was understood but names something that
    does not exist, which is a different problem from a malformed message or an unsupported action.
    """
    answer = _unlock(booted, 9)
    assert answer.error is not None
    assert answer.error.code == PROPERTY_CONSTRAINT_VIOLATION
    assert "9" in answer.error.description


def _unlock(rig: Rig, connector: int) -> Exchange:
    if rig.version == "1.6":
        return rig.order("UnlockConnector", {"connectorId": connector})
    return rig.order("UnlockConnector", {"evseId": connector, "connectorId": 1})


def _availability(rig: Rig, connector: int, operative: bool) -> Exchange:
    if rig.version == "1.6":
        return rig.order(
            "ChangeAvailability", {"connectorId": connector, "type": "Operative" if operative else "Inoperative"}
        )
    return rig.order(
        "ChangeAvailability",
        {"operationalStatus": "Operative" if operative else "Inoperative", "evse": {"id": connector, "connectorId": 1}},
    )
