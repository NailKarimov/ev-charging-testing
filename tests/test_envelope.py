"""The envelope every OCPP message travels in, and what happens to a damaged one.

These tests do not need a charge point: they work on the rules of OCPP-J itself.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from evse_lab import rpc
from evse_lab.rpc import Call, CallError, CallResult, MalformedMessage

pytestmark = pytest.mark.once


@pytest.mark.smoke
def test_a_call_is_four_parts() -> None:
    """A call on the wire is the number 2, a unique id, the name of the action and its payload."""
    call = Call("19223201", "BootNotification", {"chargePointVendor": "QA", "chargePointModel": "SIM"})
    assert json.loads(rpc.dump(call)) == [
        2,
        "19223201",
        "BootNotification",
        {"chargePointVendor": "QA", "chargePointModel": "SIM"},
    ]


def test_an_answer_carries_the_id_of_its_call() -> None:
    """An answer is the number 3 and the id of the call it answers, so both sides can match them."""
    assert json.loads(rpc.dump(CallResult("19223201", {"status": "Accepted"}))) == [
        2 + 1,
        "19223201",
        {"status": "Accepted"},
    ]


def test_a_refusal_names_a_code_and_a_reason() -> None:
    """A refusal is the number 4, the id, an error code, a description and room for details."""
    error = CallError("19223201", rpc.NOT_IMPLEMENTED, "no such action")
    assert json.loads(rpc.dump(error)) == [4, "19223201", "NotImplemented", "no such action", {}]


@pytest.mark.parametrize(
    "message",
    [
        Call("abc", "Heartbeat", {}),
        Call("x" * 36, "StatusNotification", {"connectorId": 1}),
        CallResult("abc", {}),
        CallError("abc", "GenericError", "something", {"detail": 1}),
    ],
    ids=["call", "longest-id", "answer", "refusal"],
)
def test_round_trip(message: rpc.Message) -> None:
    """Whatever is written to the wire is read back as the same message."""
    assert rpc.parse(rpc.dump(message)) == message


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        ("{not json", rpc.FORMATION_VIOLATION),
        ('{"jsonrpc": "2.0"}', rpc.FORMATION_VIOLATION),
        ("[]", rpc.FORMATION_VIOLATION),
        ('[9, "abc", "Heartbeat", {}]', rpc.PROTOCOL_ERROR),
        ('["2", "abc", "Heartbeat", {}]', rpc.PROTOCOL_ERROR),
        ('[2, "abc", "Heartbeat"]', rpc.PROTOCOL_ERROR),
        ('[2, "abc", "Heartbeat", {}, "extra"]', rpc.PROTOCOL_ERROR),
        ('[2, "", "Heartbeat", {}]', rpc.PROTOCOL_ERROR),
        ('[2, 17, "Heartbeat", {}]', rpc.PROTOCOL_ERROR),
        (f'[2, "{"x" * 37}", "Heartbeat", {{}}]', rpc.PROTOCOL_ERROR),
        ('[2, "abc", "", {}]', rpc.PROTOCOL_ERROR),
        ('[2, "abc", "Heartbeat", []]', rpc.TYPE_CONSTRAINT_VIOLATION),
        ('[3, "abc"]', rpc.PROTOCOL_ERROR),
        ('[4, "abc", "GenericError"]', rpc.PROTOCOL_ERROR),
        ('[4, "abc", 500, "boom", {}]', rpc.PROTOCOL_ERROR),
    ],
    ids=[
        "not-json",
        "json-but-not-an-array",
        "empty-array",
        "unknown-message-type",
        "message-type-as-text",
        "call-too-short",
        "call-too-long",
        "empty-id",
        "id-not-text",
        "id-too-long",
        "empty-action",
        "payload-not-an-object",
        "answer-too-short",
        "refusal-too-short",
        "error-code-not-text",
    ],
)
def test_malformed_messages_are_refused_with_the_right_code(raw: str, expected_code: str) -> None:
    """Every shape of damaged message is refused, and the refusal names the reason the standard gives."""
    with pytest.raises(MalformedMessage) as caught:
        rpc.parse(raw)
    assert caught.value.error_code == expected_code


def test_a_message_without_a_readable_id_cannot_be_answered() -> None:
    """When the id itself is unreadable there is nothing to answer to, so no id comes back."""
    with pytest.raises(MalformedMessage) as caught:
        rpc.parse('[2, 17, "Heartbeat", {}]')
    assert caught.value.message_id is None


def test_a_damaged_message_that_still_has_an_id_can_be_answered() -> None:
    """A message whose id is readable gets a refusal carrying that id."""
    with pytest.raises(MalformedMessage) as caught:
        rpc.parse('[2, "abc", "Heartbeat", []]')
    assert caught.value.message_id == "abc"


@given(
    st.text(min_size=1, max_size=36).filter(lambda s: s.strip()),
    st.sampled_from(["Heartbeat", "Authorize", "StatusNotification"]),
    st.dictionaries(st.text(min_size=1, max_size=8), st.integers() | st.text(max_size=8), max_size=5),
)
def test_any_call_survives_the_wire(message_id: str, action: str, payload: dict[str, object]) -> None:
    """Any id, action and payload written to the wire are read back unchanged."""
    call = Call(message_id, action, payload)
    assert rpc.parse(rpc.dump(call)) == call


@given(st.text(max_size=60))
def test_random_text_is_never_mistaken_for_a_message(raw: str) -> None:
    """Random text is either refused as damaged or is a valid message; it is never half-read."""
    try:
        parsed = rpc.parse(raw)
    except MalformedMessage:
        return
    assert isinstance(parsed, Call | CallResult | CallError)
