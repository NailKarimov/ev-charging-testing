"""Validation of every payload against the official OCPP JSON schemas.

The schemas are not written here: they are the ones published with the standard and shipped inside the
``ocpp`` package (78 files for OCPP 1.6, 128 for 2.0.1). They are the independent oracle of this project.
Both sides of every conversation are checked, in both directions, so a message this project builds is
accepted only if the standard would accept it.

OCPP 1.6 names its files ``Authorize.json`` and ``AuthorizeResponse.json``; 2.0.1 names them
``AuthorizeRequest.json`` and ``AuthorizeResponse.json``. ``schema_name`` hides that difference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

V16 = "1.6"
V201 = "2.0.1"
VERSIONS = (V16, V201)
PACKAGE_DIR = {V16: "v16", V201: "v201"}


class SchemaViolation(Exception):
    """A payload does not match the official schema of its message."""

    def __init__(self, version: str, name: str, errors: list[str]) -> None:
        super().__init__(f"{name} ({'OCPP ' + version}) does not match the official schema: " + "; ".join(errors))
        self.version = version
        self.name = name
        self.errors = errors


class UnknownAction(KeyError):
    """The standard of this version has no such message."""


@cache
def schema_dir(version: str) -> Path:
    import ocpp

    return Path(ocpp.__file__).resolve().parent / PACKAGE_DIR[version] / "schemas"


def schema_name(version: str, action: str, response: bool) -> str:
    if version == V16:
        return f"{action}Response" if response else action
    return f"{action}Response" if response else f"{action}Request"


@cache
def load(version: str, action: str, response: bool = False) -> dict[str, Any]:
    """The official schema of one message. Raises ``UnknownAction`` if the standard has no such message."""
    path = schema_dir(version) / f"{schema_name(version, action, response)}.json"
    if not path.exists():
        raise UnknownAction(f"OCPP {version} has no message {action!r}")
    data: dict[str, Any] = json.loads(path.read_text())
    return data


@cache
def actions(version: str) -> frozenset[str]:
    """Every message name the standard of this version defines."""
    names = set()
    for path in schema_dir(version).iterdir():
        stem = path.stem
        if stem.endswith("Response"):
            continue
        names.add(stem[: -len("Request")] if version == V201 and stem.endswith("Request") else stem)
    return frozenset(names)


@cache
def _validator(version: str, action: str, response: bool) -> jsonschema.protocols.Validator:
    schema = load(version, action, response)
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def validate(version: str, action: str, payload: dict[str, Any], response: bool = False) -> None:
    """Raise ``SchemaViolation`` if ``payload`` does not match the official schema."""
    errors = sorted(_validator(version, action, response).iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        reasons = [f"{'/'.join(str(p) for p in error.absolute_path) or '(root)'}: {error.message}" for error in errors]
        raise SchemaViolation(version, schema_name(version, action, response), reasons)


def errors_of(version: str, action: str, payload: dict[str, Any], response: bool = False) -> list[str]:
    """The schema errors of a payload, empty when it is valid. For tests that expect a violation."""
    try:
        validate(version, action, payload, response)
    except SchemaViolation as violation:
        return violation.errors
    return []


@dataclass(frozen=True)
class Required:
    """What the schema demands of a message: handy for tests that take fields away one by one."""

    fields: tuple[str, ...]
    properties: tuple[str, ...]
    additional_allowed: bool


def required(version: str, action: str, response: bool = False) -> Required:
    schema = load(version, action, response)
    return Required(
        tuple(schema.get("required", ())),
        tuple(schema.get("properties", {})),
        bool(schema.get("additionalProperties", True)),
    )
