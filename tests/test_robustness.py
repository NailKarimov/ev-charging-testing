"""Garbage, surprises and bad timing: the station must keep standing.

A charge point is on a public network. It will be sent nonsense, by broken software and by people
trying things. Whatever arrives, the rule is the same: refuse it, say why, keep the connection, and
never carry out half of it.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from evse_lab import rpc
from evse_lab.rpc import TYPE_CONSTRAINT_VIOLATION, Call, CallResult
from support.evidence import step
from support.rig import Rig

FUZZ = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


def _last_error(rig: Rig) -> rpc.CallError | None:
    return rig.cp.sent_errors[-1] if rig.cp.sent_errors else None


@pytest.mark.smoke
@pytest.mark.parametrize(
    "garbage",
    [
        "not json at all",
        "{}",
        "[]",
        "null",
        '"a string"',
        '[2, "id-1"]',
        '[2, "id-1", "Reset"]',
        '[7, "id-1", "Reset", {"type": "Soft"}]',
        '[2, "id-1", "Reset", "not an object"]',
        '[3, "never-asked", {"status": "Accepted"}]',
        '[4, "never-asked", "GenericError", "boom", {}]',
    ],
    ids=[
        "not-json",
        "an-object-not-an-array",
        "an-empty-array",
        "null",
        "a-bare-string",
        "a-call-with-no-action",
        "a-call-with-no-payload",
        "an-unknown-message-type",
        "a-payload-that-is-not-an-object",
        "an-answer-to-nothing",
        "a-refusal-of-nothing",
    ],
)
def test_garbage_never_takes_the_station_down(loose: Rig, garbage: str) -> None:
    """Whatever nonsense arrives, the station survives it and answers the next real order."""
    loose.boot()
    with step(f"The back office sends: {garbage[:60]}"):
        loose.back.send_raw(garbage)
        loose.talk.settle()
    answer = loose.order("TriggerMessage", {"requestedMessage": "StatusNotification"})
    assert answer.payload["status"] == "Accepted", "the station stopped answering after the garbage"


def test_a_damaged_message_with_a_readable_id_is_refused_with_that_id(loose: Rig) -> None:
    """When the id can still be read, the refusal carries it so the sender knows which message failed."""
    loose.boot()
    loose.back.send_raw('[2, "my-id-42", "Reset", "not an object"]')
    loose.talk.settle()
    error = _last_error(loose)
    assert error is not None
    assert error.id == "my-id-42"
    assert error.code == TYPE_CONSTRAINT_VIOLATION, "the refusal names what was wrong, not just that something was"


def test_a_message_whose_id_is_unreadable_gets_no_answer(loose: Rig) -> None:
    """With no readable id there is nothing to answer to, so the station stays silent and carries on."""
    loose.boot()
    before = len(loose.cp.sent_errors)
    loose.back.send_raw('[2, 17, "Reset", {"type": "Soft"}]')
    loose.talk.settle()
    assert len(loose.cp.sent_errors) == before
    assert loose.order("TriggerMessage", {"requestedMessage": "Heartbeat"}).payload["status"] == "Accepted"


def test_an_answer_to_a_message_nobody_sent_is_ignored(loose: Rig) -> None:
    """An answer whose id matches no outstanding call is dropped without disturbing anything."""
    loose.boot()
    loose.start_session()
    loose.back.send_raw(json.dumps(CallResult("made-up-id", {"status": "Accepted"}).to_list()))
    loose.talk.settle()
    loose.charge(120, step=60)
    assert loose.csms.session_of("CARD-OK").running
    assert loose.station.connector(1).transaction is not None


def test_an_answer_with_the_wrong_id_is_not_taken_for_the_one_being_waited_for(loose: Rig) -> None:
    """While waiting for an answer, one that carries a different id is ignored, not mistaken for it."""
    loose.boot()
    loose.start_session()
    loose.station.advance(120)
    loose.cp.refresh()
    loose.cp.send_next()
    outstanding = loose.cp.outstanding
    assert outstanding is not None
    with step("An answer arrives carrying somebody else's id"):
        loose.back.send_raw(json.dumps(CallResult("not-the-id-you-want", {}).to_list()))
        loose.cp.step()
    assert loose.cp.outstanding is outstanding, "the station is still waiting for its own answer"
    loose.talk.settle()
    assert loose.cp.outstanding is None, "and it takes the right answer when it comes"


def test_a_very_long_message_is_refused_without_a_crash(loose: Rig) -> None:
    """A message far larger than any real one is refused and the connection keeps working."""
    loose.boot()
    huge = Call("big-1", "Reset", {"type": "Soft", "padding": "x" * 50_000})
    loose.back.send_raw(json.dumps(huge.to_list()))
    loose.talk.settle()
    assert _last_error(loose) is not None
    assert loose.station.reset_pending is None
    assert loose.order("TriggerMessage", {"requestedMessage": "Heartbeat"}).payload["status"] == "Accepted"


def test_a_deeply_nested_payload_is_refused(loose: Rig) -> None:
    """A payload nested hundreds of levels deep is refused by the schema, not followed down."""
    loose.boot()
    nested: object = "bottom"
    for _ in range(200):
        nested = {"next": nested}
    loose.back.send_raw(json.dumps(Call("deep-1", "Reset", {"type": nested}).to_list()))
    loose.talk.settle()
    assert _last_error(loose) is not None
    assert loose.station.reset_pending is None


@given(st.text(max_size=120))
@FUZZ
def test_any_text_at_all_leaves_the_station_answering(loose: Rig, garbage: str) -> None:
    """Send the station any text at all: it still answers the order that comes next."""
    loose.station.booted = True
    loose.back.send_raw(garbage)
    loose.talk.settle()
    assert loose.order("TriggerMessage", {"requestedMessage": "Heartbeat"}).payload["status"] == "Accepted"


@given(
    st.lists(
        st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-10, max_value=10),
            st.text(max_size=12),
            st.dictionaries(st.text(max_size=6), st.integers(), max_size=3),
        ),
        max_size=6,
    )
)
@FUZZ
def test_any_json_array_is_either_a_message_or_refused(loose: Rig, body: list[object]) -> None:
    """Any JSON array either is a valid message or is refused; nothing in between is acted on."""
    loose.station.booted = True
    before = loose.station.reset_pending
    loose.back.send_raw(json.dumps(body))
    loose.talk.settle()
    assert loose.station.reset_pending == before
    assert loose.order("TriggerMessage", {"requestedMessage": "Heartbeat"}).payload["status"] == "Accepted"


@given(
    st.sampled_from(["Reset", "ChangeAvailability", "UnlockConnector", "TriggerMessage"]),
    st.dictionaries(st.text(min_size=1, max_size=8), st.integers() | st.text(max_size=8) | st.booleans(), max_size=4),
)
@FUZZ
def test_a_known_order_with_made_up_fields_is_refused_not_guessed(
    loose: Rig, action: str, payload: dict[str, object]
) -> None:
    """An order of a known kind but with invented fields is refused rather than half understood."""
    loose.station.booted = True
    answer = loose.order(action, payload)
    assert answer.error is not None or answer.payload.get("status") in (
        "Accepted",
        "Rejected",
        "Scheduled",
        "Unlocked",
        "UnlockFailed",
        "NotSupported",
        "NotImplemented",
        "UnknownConnector",
    )


def test_an_order_that_arrives_while_the_station_is_waiting_is_answered(booted: Rig) -> None:
    """The station answers an order even while it is waiting for an answer of its own."""
    booted.start_session()
    booted.station.advance(120)
    booted.cp.refresh()
    booted.cp.send_next()  # a meter reading is now on the wire, unanswered
    assert booted.cp.outstanding is not None
    with step("An order arrives while the station is waiting for an answer"):
        answer = booted.order("TriggerMessage", {"requestedMessage": "Heartbeat"})
    assert answer.payload["status"] == "Accepted"
    booted.talk.settle()
    assert booted.cp.outstanding is None, "the station's own message was answered too"


def test_the_station_sends_one_message_at_a_time(booted: Rig) -> None:
    """The station never has two of its own messages outstanding: it sends one and waits for the answer.

    OCPP 1.6 requires this, and it is what makes the queue after an outage meaningful: a message is only
    built once the one before it has been answered.
    """
    booted.start_session()
    booted.station.advance(400)  # six meter readings are now due
    booted.cp.refresh()
    assert len(booted.cp.pending) <= 1, "only the next message is built, the rest wait as events"
    assert booted.cp.waiting, "the others are still events, not messages"

    for _ in range(5):
        assert booted.cp.send_next(), "there is still something to send"
        assert booted.cp.outstanding is not None
        assert booted.cp.pending == [], "nothing else is queued up behind it"
        assert not booted.cp.send_next(), "a second message must not go out before the first is answered"
        booted.back.step()  # the back office reads it and answers
        booted.cp.step()  # the station takes the answer
        assert booted.cp.outstanding is None
