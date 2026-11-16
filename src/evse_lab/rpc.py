"""The OCPP-J envelope: how an OCPP message is wrapped before it goes down the WebSocket.

Every OCPP message is a JSON array whose first element says what kind of message it is:

    [2, "19223201", "BootNotification", {...}]        a call: id, action, payload
    [3, "19223201", {...}]                            the answer to that call
    [4, "19223201", "NotSupported", "...", {...}]     a refusal: error code, description, details

The id ties an answer to its call, so both sides may have several calls in flight. The rules here are
from OCPP-J 1.6 (chapter 4) and OCPP 2.0.1 part 4; they are the same in both versions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

MAX_ID_LENGTH = 36  # OCPP-J: the unique id is at most 36 characters
MAX_ACTION_LENGTH = 36


class MessageType(IntEnum):
    CALL = 2
    CALL_RESULT = 3
    CALL_ERROR = 4


# RPC framework errors, OCPP-J 1.6 table 2 and OCPP 2.0.1 part 4 table 11.
FORMATION_VIOLATION = "FormationViolation"
PROTOCOL_ERROR = "ProtocolError"
PROPERTY_CONSTRAINT_VIOLATION = "PropertyConstraintViolation"
TYPE_CONSTRAINT_VIOLATION = "TypeConstraintViolation"
OCCURRENCE_CONSTRAINT_VIOLATION = "OccurrenceConstraintViolation"
NOT_IMPLEMENTED = "NotImplemented"
NOT_SUPPORTED = "NotSupported"
INTERNAL_ERROR = "InternalError"
SECURITY_ERROR = "SecurityError"
GENERIC_ERROR = "GenericError"


class MalformedMessage(ValueError):
    """The bytes on the wire are not an OCPP message at all.

    ``error_code`` is what the receiver must answer with when the message id could still be read;
    a message whose id cannot be read gets no answer, because there is nothing to answer to.
    """

    def __init__(self, reason: str, error_code: str = FORMATION_VIOLATION, message_id: str | None = None) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.message_id = message_id


@dataclass(frozen=True)
class Call:
    id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_list(self) -> list[Any]:
        return [int(MessageType.CALL), self.id, self.action, self.payload]


@dataclass(frozen=True)
class CallResult:
    id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_list(self) -> list[Any]:
        return [int(MessageType.CALL_RESULT), self.id, self.payload]


@dataclass(frozen=True)
class CallError:
    id: str
    code: str
    description: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_list(self) -> list[Any]:
        return [int(MessageType.CALL_ERROR), self.id, self.code, self.description, self.details]


Message = Call | CallResult | CallError


def dump(message: Message) -> str:
    """The message as the text that goes on the wire."""
    return json.dumps(message.to_list(), separators=(",", ":"))


def parse(raw: str | bytes) -> Message:
    """Read one message from the wire. Raises ``MalformedMessage`` if it is not one.

    The checks follow the order a receiver can make them: is it JSON, is it an array, does it start
    with a known message type, is the id usable, and only then the fields of that type.
    """
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MalformedMessage(f"not JSON: {exc}") from exc
    if not isinstance(body, list):
        raise MalformedMessage(f"not a JSON array but {type(body).__name__}")
    if not body:
        raise MalformedMessage("empty array")

    kind = body[0]
    if not isinstance(kind, int) or isinstance(kind, bool) or kind not in tuple(MessageType):
        raise MalformedMessage(f"unknown message type {kind!r}", PROTOCOL_ERROR)

    if len(body) < 2 or not isinstance(body[1], str) or not body[1]:
        raise MalformedMessage("missing or unusable message id", PROTOCOL_ERROR)
    message_id = body[1]
    if len(message_id) > MAX_ID_LENGTH:
        raise MalformedMessage(f"message id longer than {MAX_ID_LENGTH} characters", PROTOCOL_ERROR, message_id)

    if kind == MessageType.CALL:
        if len(body) != 4:
            raise MalformedMessage(f"a call has 4 elements, got {len(body)}", PROTOCOL_ERROR, message_id)
        action, payload = body[2], body[3]
        if not isinstance(action, str) or not action:
            raise MalformedMessage("missing action", PROTOCOL_ERROR, message_id)
        if len(action) > MAX_ACTION_LENGTH:
            raise MalformedMessage("action name too long", PROTOCOL_ERROR, message_id)
        if not isinstance(payload, dict):
            raise MalformedMessage("payload is not an object", TYPE_CONSTRAINT_VIOLATION, message_id)
        return Call(message_id, action, payload)

    if kind == MessageType.CALL_RESULT:
        if len(body) != 3:
            raise MalformedMessage(f"an answer has 3 elements, got {len(body)}", PROTOCOL_ERROR, message_id)
        if not isinstance(body[2], dict):
            raise MalformedMessage("payload is not an object", TYPE_CONSTRAINT_VIOLATION, message_id)
        return CallResult(message_id, body[2])

    if len(body) < 4:
        raise MalformedMessage(f"a refusal has at least 4 elements, got {len(body)}", PROTOCOL_ERROR, message_id)
    code, description = body[2], body[3]
    if not isinstance(code, str) or not isinstance(description, str):
        raise MalformedMessage("error code and description must be text", PROTOCOL_ERROR, message_id)
    details = body[4] if len(body) > 4 else {}
    if not isinstance(details, dict):
        raise MalformedMessage("error details are not an object", TYPE_CONSTRAINT_VIOLATION, message_id)
    return CallError(message_id, code, description, details)
