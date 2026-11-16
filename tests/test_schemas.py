"""The official OCPP schemas as the oracle: every message this project builds must satisfy them.

The schemas are not written here. They are the ones published with the standard and shipped inside the
``ocpp`` package: 39 messages for OCPP 1.6 and 64 for 2.0.1, each with a request and a response schema.
If this project and the standard ever disagree, these tests say so.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from evse_lab import schemas
from evse_lab.schemas import V16, V201, SchemaViolation

pytestmark = pytest.mark.once


@pytest.mark.smoke
@pytest.mark.parametrize(("version", "count"), [(V16, 39), (V201, 64)])
def test_the_standard_brings_its_own_schemas(version: str, count: int) -> None:
    """Both versions come with a schema for every message, a request and a response for each."""
    actions = schemas.actions(version)
    assert len(actions) == count
    for action in actions:
        assert schemas.load(version, action)
        assert schemas.load(version, action, response=True)


def test_a_message_the_standard_does_not_have_is_an_error() -> None:
    """Asking for the schema of an invented message fails instead of passing anything."""
    with pytest.raises(schemas.UnknownAction):
        schemas.load(V16, "MakeCoffee")


@pytest.mark.parametrize(
    ("version", "action", "payload"),
    [
        (V16, "BootNotification", {"chargePointVendor": "QA", "chargePointModel": "SIM"}),
        (V16, "Authorize", {"idTag": "CARD-OK"}),
        (V16, "StatusNotification", {"connectorId": 1, "errorCode": "NoError", "status": "Available"}),
        (V201, "BootNotification", {"reason": "PowerUp", "chargingStation": {"model": "SIM", "vendorName": "QA"}}),
        (V201, "Authorize", {"idToken": {"idToken": "CARD-OK", "type": "ISO14443"}}),
    ],
)
def test_a_correct_message_passes(version: str, action: str, payload: dict[str, object]) -> None:
    """A message built as the standard describes is accepted."""
    schemas.validate(version, action, payload)


@pytest.mark.parametrize(
    ("version", "action", "payload", "expected"),
    [
        (V16, "Authorize", {}, "idTag"),
        (V16, "Authorize", {"idTag": 17}, "not of type"),
        (V16, "Authorize", {"idTag": "x" * 21}, "too long"),
        (V16, "Authorize", {"idTag": "CARD-OK", "extra": 1}, "Additional properties"),
        (V16, "StatusNotification", {"connectorId": 1, "errorCode": "NoError", "status": "Dancing"}, "is not one of"),
        (V201, "Authorize", {"idToken": {"idToken": "CARD-OK"}}, "type"),
        (V201, "BootNotification", {"reason": "PowerUp"}, "chargingStation"),
    ],
    ids=[
        "missing-required-field",
        "wrong-type",
        "text-too-long",
        "unknown-field",
        "value-outside-the-list",
        "missing-field-in-a-nested-object",
        "missing-nested-object",
    ],
)
def test_a_wrong_message_is_rejected_and_the_reason_is_named(
    version: str, action: str, payload: dict[str, object], expected: str
) -> None:
    """Each kind of mistake is refused by the official schema, and the reason says which field is wrong."""
    errors = schemas.errors_of(version, action, payload)
    assert errors, f"the schema accepted {payload}"
    assert any(expected in error for error in errors), errors


def test_neither_version_bounds_the_connector_number_in_its_schema() -> None:
    """Neither version limits the connector number in its schema, so a station cannot rely on the schema.

    ``connectorId`` in 1.6 and ``evseId`` in 2.0.1 are both declared as a plain ``integer``: -1, 0 and
    9999 all pass the official schema. The station has to check the number itself, and
    ``test_remote_commands`` proves that it does.
    """
    assert schemas.load(V16, "StatusNotification")["properties"]["connectorId"] == {"type": "integer"}
    assert schemas.load(V201, "StatusNotification")["properties"]["evseId"]["type"] == "integer"
    assert "minimum" not in schemas.load(V201, "StatusNotification")["properties"]["evseId"]
    for number in (-1, 0, 9999):
        assert (
            schemas.errors_of(
                V16,
                "StatusNotification",
                {
                    "connectorId": number,
                    "errorCode": "NoError",
                    "status": "Available",
                },
            )
            == []
        )
        assert (
            schemas.errors_of(
                V201,
                "StatusNotification",
                {
                    "timestamp": "2026-06-15T08:00:00Z",
                    "connectorStatus": "Available",
                    "evseId": number,
                    "connectorId": 1,
                },
            )
            == []
        )


def test_the_two_versions_disagree_about_a_meter_reading() -> None:
    """A meter reading of OCPP 1.6 is refused by the 2.0.1 schema and the other way round: value and unit differ."""
    from evse_lab.dialect import sampled_value

    old = sampled_value(V16, 4500000, 3680.0, 16.0)
    new = sampled_value(V201, 4500000, 3680.0, 16.0)
    assert isinstance(old[0]["value"], str)
    assert old[0]["unit"] == "Wh"
    assert isinstance(new[0]["value"], float)
    assert new[0]["unitOfMeasure"] == {"unit": "Wh"}

    as_201 = {
        "eventType": "Updated",
        "timestamp": "2026-06-15T08:00:00Z",
        "triggerReason": "MeterValuePeriodic",
        "seqNo": 1,
        "transactionInfo": {"transactionId": "TX-1"},
        "meterValue": [{"timestamp": "2026-06-15T08:00:00Z", "sampledValue": old}],
    }
    assert schemas.errors_of(V201, "TransactionEvent", as_201)

    as_16 = {"connectorId": 1, "meterValue": [{"timestamp": "2026-06-15T08:00:00Z", "sampledValue": new}]}
    assert schemas.errors_of(V16, "MeterValues", as_16)


@pytest.mark.parametrize("version", [V16, V201])
def test_every_schema_is_strict_about_unknown_fields(version: str) -> None:
    """No message of either version silently accepts a field the standard does not define."""
    loose = []
    for action in sorted(schemas.actions(version)):
        for response in (False, True):
            if schemas.load(version, action, response).get("additionalProperties", True):
                loose.append(schemas.schema_name(version, action, response))
    assert loose == [], f"these schemas would accept unknown fields: {loose}"


@pytest.mark.parametrize("version", [V16, V201])
def test_required_fields_are_really_required(version: str) -> None:
    """For every message, leaving out a required field is refused. 103 messages are checked in all."""
    checked = 0
    for action in sorted(schemas.actions(version)):
        wanted = schemas.required(version, action)
        if not wanted.fields:
            continue
        checked += 1
        for field in wanted.fields:
            payload = {name: _example(version, action, name) for name in wanted.fields if name != field}
            with pytest.raises(SchemaViolation):
                schemas.validate(version, action, payload)
    assert checked > 20


def _example(version: str, action: str, field: str) -> Any:
    """A value of roughly the right type, enough to leave one field out and see it noticed."""
    schema = schemas.load(version, action)
    properties = schema.get("properties", {})
    assert isinstance(properties, dict)
    spec = properties.get(field, {})
    spec = _resolve(schema, spec)
    kind = spec.get("type")
    if "enum" in spec:
        return spec["enum"][0]
    if kind == "string":
        return "2026-06-15T08:00:00Z" if "date-time" in str(spec.get("format", "")) else "x"
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    return {}


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    ref = spec.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        definitions = schema.get("definitions", {})
        assert isinstance(definitions, dict)
        found = definitions.get(ref.split("/")[-1], {})
        if isinstance(found, dict):
            return found
    return spec


def test_the_schemas_are_the_ones_published_with_the_standard() -> None:
    """The schema files come from the ocpp package, not from this repository."""
    directory = schemas.schema_dir(V201)
    assert "ocpp" in directory.parts, directory
    boot = json.loads((directory / "BootNotificationRequest.json").read_text())
    assert boot["comment"] == "OCPP 2.0.1 FINAL"
    assert boot["$schema"] == "http://json-schema.org/draft-06/schema#"
    old = json.loads((schemas.schema_dir(V16) / "BootNotification.json").read_text())
    assert old["title"] == "BootNotificationRequest"
    assert old["$schema"] == "http://json-schema.org/draft-04/schema#"
