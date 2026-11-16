"""Who is allowed to charge: cards the back office accepts, blocks, or has never heard of.

Authorisation decides who pays. A station that starts charging for a blocked card gives away energy; one
that refuses a good card leaves a driver stranded at a motorway stop.
"""

from __future__ import annotations

import pytest

from evse_lab.station import AuthStatus, Status
from support.evidence import step
from support.rig import Rig


@pytest.mark.smoke
def test_a_good_card_starts_the_session(booted: Rig) -> None:
    """A card the back office accepts starts the session and the car begins to charge."""
    booted.plug_in()
    with step("A valid card is held to the reader"):
        assert booted.card("CARD-OK") is AuthStatus.ACCEPTED
    booted.car_ready()
    booted.charge(60)
    assert booted.station.connector(1).status is Status.CHARGING
    assert booted.csms.session_of("CARD-OK").running


@pytest.mark.parametrize(
    ("card", "expected"),
    [
        ("CARD-BLOCKED", AuthStatus.BLOCKED),
        ("CARD-EXPIRED", AuthStatus.EXPIRED),
        ("CARD-NOBODY-KNOWS", AuthStatus.INVALID),
    ],
    ids=["blocked", "expired", "unknown"],
)
def test_a_card_the_back_office_does_not_accept_starts_nothing(booted: Rig, card: str, expected: AuthStatus) -> None:
    """A blocked, expired or unknown card is refused and no session begins."""
    booted.plug_in()
    with step(f"The card {card} is held to the reader"):
        assert booted.card(card) is expected
    booted.car_ready()
    booted.charge(60)
    assert booted.station.connector(1).transaction is None
    assert booted.csms.sessions == {}
    assert booted.station.connector(1).status is Status.PREPARING
    assert booted.station.power_w(1) == 0.0


def test_the_station_asks_the_back_office_before_it_lets_anyone_charge(booted: Rig) -> None:
    """Before a session starts the station asks the back office about the card and sends the card number."""
    booted.plug_in()
    booted.card("CARD-OK")
    asked = booted.cp.last("Authorize")
    sent = asked.call.payload
    number = sent.get("idTag") or sent["idToken"]["idToken"]
    assert number == "CARD-OK"
    assert booted.cp.sent_actions().index("Authorize") < len(booted.cp.sent_actions())


def test_the_same_card_stops_the_session_it_started(booted: Rig) -> None:
    """Holding the same card to the reader again ends the session the driver started."""
    booted.start_session("CARD-OK")
    booted.charge(300, step=60)
    with step("The same card is held to the reader again"):
        booted.card("CARD-OK")
        booted.charge(2)
    session = booted.csms.session_of("CARD-OK")
    assert not session.running
    assert session.reason == "Local"


def test_another_card_cannot_stop_somebody_else_s_session(booted: Rig) -> None:
    """A different card does not end a session it did not start: a stranger cannot stop your charge."""
    booted.start_session("CARD-OK")
    booted.charge(300, step=60)
    with step("A different valid card is held to the reader"):
        assert booted.card("CARD-OK-2") is AuthStatus.CONCURRENT_TX
    booted.charge(60)
    assert booted.csms.session_of("CARD-OK").running
    assert booted.station.connector(1).status is Status.CHARGING


def test_a_refused_card_can_be_followed_by_a_good_one(booted: Rig) -> None:
    """After a refused card the reader still works: the next good card starts the session."""
    booted.plug_in()
    booted.card("CARD-BLOCKED")
    booted.card("CARD-OK")
    booted.car_ready()
    booted.charge(60)
    assert booted.csms.session_of("CARD-OK").running


def test_the_answer_about_a_card_uses_the_words_of_its_version(booted: Rig) -> None:
    """The back office answers about a card in the shape its version defines, down to the field names."""
    booted.plug_in()
    booted.card("CARD-OK")
    answer = booted.cp.last("Authorize").payload
    if booted.version == "1.6":
        assert answer["idTagInfo"]["status"] == "Accepted"
    else:
        assert answer["idTokenInfo"]["status"] == "Accepted"


def test_an_unknown_card_is_reported_differently_by_the_two_versions(booted: Rig) -> None:
    """OCPP 1.6 calls an unknown card Invalid; 2.0.1 renamed that answer to Unknown."""
    booted.plug_in()
    booted.card("CARD-NOBODY-KNOWS")
    answer = booted.cp.last("Authorize").payload
    if booted.version == "1.6":
        assert answer["idTagInfo"]["status"] == "Invalid"
    else:
        assert answer["idTokenInfo"]["status"] == "Unknown"


def test_a_card_on_one_connector_does_not_touch_the_other(booted: Rig) -> None:
    """A card presented at one connector starts a session there and leaves the other connector free."""
    booted.plug_in(1)
    booted.plug_in(2)
    booted.card("CARD-OK", connector=1)
    booted.charge(10)
    assert booted.station.connector(1).transaction is not None
    assert booted.station.connector(2).transaction is None
    assert booted.station.connector(2).status is Status.PREPARING
