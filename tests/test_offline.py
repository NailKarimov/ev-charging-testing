"""What happens when the link to the back office goes down in the middle of a charging session.

This is the part of OCPP that costs money when it is wrong. A charge point in a car park keeps charging
whether or not the back office can be reached, and everything it owes has to arrive later, once, in
order. A lost session is energy nobody is billed for; a doubled one is a customer billed twice.
"""

from __future__ import annotations

import json

import pytest

from evse_lab.station import Status
from support.evidence import step
from support.rig import Rig


@pytest.mark.smoke
def test_charging_carries_on_while_the_back_office_is_unreachable(booted: Rig) -> None:
    """When the link drops the car keeps charging: the meter goes on counting and the session stays open."""
    booted.start_session()
    booted.charge(120)
    before = int(booted.station.meter_wh)
    with step("The link to the back office goes down"):
        booted.offline()
        booted.charge(600, step=60)
    assert booted.station.connector(1).status is Status.CHARGING
    assert int(booted.station.meter_wh) > before
    assert booted.station.connector(1).transaction is not None


def test_nothing_is_sent_while_the_link_is_down(booted: Rig) -> None:
    """While the link is down the station sends nothing and keeps what it owes."""
    booted.start_session()
    sent = len(booted.cp.history)
    booted.offline()
    booted.charge(600, step=60)
    assert len(booted.cp.history) == sent, "the station must not pretend the back office is listening"
    assert len(booted.station.queue) >= 10


def test_everything_owed_arrives_when_the_link_comes_back(booted: Rig) -> None:
    """After the link returns, every message kept during the outage is delivered, oldest first."""
    booted.start_session()
    booted.charge(60)
    with step("The link goes down for ten minutes of charging"):
        booted.offline()
        booted.charge(600, step=60)
    owed = len(booted.station.queue)
    with step("The link comes back"):
        booted.online()
    assert booted.station.queue == []
    samples = booted.csms.session_of("CARD-OK").samples
    assert len(samples) >= 10
    assert samples == sorted(samples), "the readings arrive in the order they were taken"
    assert owed >= 10


@pytest.mark.smoke
def test_a_session_that_begins_and_ends_during_an_outage_is_billed_once(booted: Rig) -> None:
    """A whole session with the link down reaches the back office once, complete, when the link returns."""
    with step("The link goes down before the driver arrives"):
        booted.offline()
    with step("The driver plugs in and holds a card the station has seen before"):
        booted.station.auth_cache["CARD-OK"] = booted.station.known_tags["CARD-OK"]
        booted.plug_in()
        booted.card("CARD-OK")
        booted.car_ready()
    with step("Twenty minutes of charging with nobody listening"):
        booted.charge(1200, step=120)
    with step("The driver stops and leaves"):
        booted.card("CARD-OK")
        booted.unplug()
        booted.charge(2)
    assert booted.csms.sessions == {}, "nothing can have reached the back office yet"
    with step("The link comes back"):
        booted.online()

    assert len(booted.csms.sessions) == 1, "exactly one session, not none and not two"
    session = booted.csms.session_of("CARD-OK")
    assert not session.running
    assert session.energy_wh == pytest.approx(1226, abs=3)
    assert session.reason == "Local"


def test_the_end_of_a_session_carries_the_number_the_back_office_gave_it(booted: Rig) -> None:
    """The message that ends a session names the same transaction as the one that started it."""
    booted.offline()
    booted.station.auth_cache["CARD-OK"] = booted.station.known_tags["CARD-OK"]
    booted.plug_in()
    booted.card("CARD-OK")
    booted.car_ready()
    booted.charge(300, step=60)
    booted.card("CARD-OK")
    booted.charge(2)
    booted.online()

    session = booted.csms.session_of("CARD-OK")
    assert not session.running, "the back office must be able to match the end to the start"
    keys = _transaction_keys(booted)
    assert len(set(keys)) == 1, f"the session was reported under more than one number: {sorted(set(keys))}"
    assert keys[0] == session.key


def test_an_outage_between_two_messages_does_not_split_the_session(booted: Rig) -> None:
    """A link that drops and returns twice during one session still leaves exactly one session behind."""
    booted.start_session()
    for _ in range(2):
        booted.charge(120, step=60)
        booted.offline()
        booted.charge(300, step=60)
        booted.online()
    booted.card("CARD-OK")
    booted.charge(2)
    assert len(booted.csms.sessions) == 1
    assert not booted.csms.session_of("CARD-OK").running


def test_a_card_the_station_has_seen_before_works_while_the_link_is_down(booted: Rig) -> None:
    """With the link down the station lets a card through if the back office accepted it before."""
    with step("The card is used once while the link is up"):
        booted.plug_in()
        booted.card("CARD-OK")
        booted.card("CARD-OK")  # stop again
        booted.unplug()
        booted.charge(2)
    with step("The link goes down and the same card comes back"):
        booted.offline()
        booted.plug_in()
        assert booted.card("CARD-OK").value == "Accepted"
    assert booted.station.connector(1).transaction is not None


def test_an_unknown_card_is_refused_while_the_link_is_down(booted: Rig) -> None:
    """With the link down a card the station has never seen is refused: it cannot ask anyone."""
    booted.offline()
    booted.plug_in()
    assert booted.card("CARD-NEVER-SEEN").value == "Invalid"
    assert booted.station.connector(1).transaction is None
    assert booted.station.connector(1).status is Status.PREPARING


def test_a_card_the_back_office_blocked_stays_blocked_while_the_link_is_down(booted: Rig) -> None:
    """A card the back office refused earlier is still refused when nobody can be asked."""
    booted.plug_in()
    assert booted.card("CARD-BLOCKED").value == "Blocked"
    booted.offline()
    assert booted.card("CARD-BLOCKED").value == "Blocked"
    assert booted.station.connector(1).transaction is None


@pytest.mark.v201
def test_in_2_0_1_the_messages_kept_during_an_outage_say_so(booted: Rig) -> None:
    """OCPP 2.0.1 marks every event that happened while the station was offline, so a gap is visible."""
    booted.start_session()
    booted.charge(60)
    booted.offline()
    booted.charge(300, step=60)
    booted.online()
    booted.card("CARD-OK")
    booted.charge(2)
    session = booted.csms.session_of("CARD-OK")
    assert session.offline_events >= 5
    assert session.seq_nos == list(range(len(session.seq_nos))), "no number is missing"


def _transaction_keys(rig: Rig) -> list[str]:
    """The transaction id in every session message the back office received."""
    keys = []
    for call in rig.csms.received:
        if call.action in ("StartTransaction",):
            keys.append(str(next(iter(rig.csms.sessions))))
        elif call.action in ("StopTransaction", "MeterValues"):
            if "transactionId" in call.payload:
                keys.append(str(call.payload["transactionId"]))
        elif call.action == "TransactionEvent":
            keys.append(str(call.payload["transactionInfo"]["transactionId"]))
    return keys


def _dump(rig: Rig) -> str:
    return json.dumps([c.action for c in rig.csms.received])
