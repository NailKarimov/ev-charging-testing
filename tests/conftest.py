"""Fixtures and hooks shared by all tests.

Two environment variables decide what a run exercises:

- ``OCPP``  ``1.6`` (default) or ``2.0.1`` — which version of the protocol is spoken
- ``LINK``  ``memory`` (default) or ``websocket`` — in one process, or across a real WebSocket

The behaviour tests are written once and run on both versions, because the station and the back office
are the same underneath; only the messages differ. Every test has a docstring of one plain sentence:
it becomes the title in the Allure report and on the visual page. The run is saved to
``evidence/run-<version>-<link>.json``.
"""

from __future__ import annotations

import os
import platform
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import allure
import pytest

from evse_lab import __version__
from evse_lab.schemas import V16, V201
from support.evidence import RECORDER, TestRecord
from support.rig import Rig, build

OCPP = os.getenv("OCPP", V16)
LINK = os.getenv("LINK", "memory")
ROOT = Path(__file__).resolve().parent.parent

if OCPP not in (V16, V201):
    raise pytest.UsageError(f"OCPP={OCPP!r}: use 1.6 or 2.0.1")
if LINK not in ("memory", "websocket"):
    raise pytest.UsageError(f"LINK={LINK!r}: use memory or websocket")

SKIP_RULES: tuple[tuple[str, Callable[[], bool], str], ...] = (
    (
        "once",
        lambda: not (OCPP == V16 and LINK == "memory"),
        "does not depend on the version: runs once, in the OCPP 1.6 run",
    ),
    ("v16", lambda: OCPP != V16, "OCPP 1.6 only"),
    ("v201", lambda: OCPP != V201, "OCPP 2.0.1 only"),
    ("websocket", lambda: LINK != "websocket", "needs a real WebSocket: run with LINK=websocket"),
    (
        "interop",
        lambda: LINK != "websocket",
        "the third-party library connects over a WebSocket: run with LINK=websocket",
    ),
)

# A WebSocket run proves the transport: the handshake, real frames and a connection that is cut. The
# behaviour of the station is the same whichever way the bytes travel, and is covered by the two
# in-memory runs, so it is not repeated here.
TRANSPORT_ONLY = ("websocket", "interop")


# ---- collection: plain titles and skips that depend on the run -------------------------------


def plain_title(item: pytest.Item) -> str:
    doc = getattr(getattr(item, "function", None), "__doc__", None) or ""
    return " ".join(doc.strip().split("\n\n")[0].split())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    untitled = [item.nodeid for item in items if not plain_title(item)]
    if untitled:
        raise pytest.UsageError("tests without a one-sentence docstring:\n  " + "\n  ".join(untitled))
    skip_behaviour = pytest.mark.skip(
        reason="the behaviour is covered by the in-memory runs; this run tests the transport"
    )
    for item in items:
        for marker, applies, reason in SKIP_RULES:
            if marker in item.keywords and applies():
                item.add_marker(pytest.mark.skip(reason=reason))
                break
        else:
            if LINK == "websocket" and not any(m in item.keywords for m in TRANSPORT_ONLY):
                item.add_marker(skip_behaviour)


# ---- evidence ---------------------------------------------------------------------------------


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    name = getattr(item, "originalname", None) or item.name
    params = item.name[len(name) :].strip("[]") if item.name != name else ""
    suite = Path(str(item.fspath)).stem
    RECORDER.start(TestRecord(id=item.nodeid, name=name, suite=suite, title=plain_title(item), params=params))
    yield
    RECORDER.current = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()  # type: ignore[attr-defined]
    record = RECORDER.current
    if record is None:
        return
    if report.when == "call":
        record.ms = report.duration * 1000
    if report.skipped and record.outcome == "passed":
        record.outcome = "skipped"
        longrepr = report.longrepr
        record.reason = str(longrepr[2] if isinstance(longrepr, tuple) else longrepr).removeprefix("Skipped: ")
    elif report.failed:
        record.outcome = "failed"
        record.reason = str(call.excinfo.value).splitlines()[0][:300] if call.excinfo else report.when


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    records = RECORDER.records
    if not records or os.getenv("EVIDENCE", "1") == "0":
        return
    counts = {key: sum(r.outcome == key for r in records) for key in ("passed", "failed", "skipped")}
    meta = {
        "version": OCPP,
        "link": LINK,
        "finished": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "release": __version__,
        "python": platform.python_version(),
        "commit": os.getenv("GITHUB_SHA", "")[:7],
        "run": os.getenv("GITHUB_RUN_NUMBER", ""),
        "tests": len(records),
        **counts,
        "frames": sum(r.frames for r in records),
    }
    out = ROOT / "evidence"
    out.mkdir(exist_ok=True)
    payload = {"meta": meta, "tests": [r.as_dict() for r in records]}
    name = f"run-{OCPP}-{LINK}.json"
    (out / name).write_text(__import__("json").dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


@pytest.fixture(autouse=True)
def _report_labels(request: pytest.FixtureRequest) -> None:
    allure.dynamic.title(plain_title(request.node))
    allure.dynamic.parameter("ocpp", OCPP)
    allure.dynamic.parameter("link", LINK)
    allure.dynamic.parent_suite(f"OCPP {OCPP} over {LINK}")


# ---- the charge point and the back office -----------------------------------------------------


@pytest.fixture
def rig() -> Iterator[Rig]:
    """A charge point connected to a back office, freshly powered on for every test."""
    built = build(OCPP, LINK)
    yield built
    built.close()


@pytest.fixture
def booted(rig: Rig) -> Rig:
    """The same, already registered with the back office and idle."""
    rig.boot()
    return rig


@pytest.fixture
def loose() -> Iterator[Rig]:
    """A rig that does not check payloads against the schema, for tests that send something wrong."""
    built = build(OCPP, LINK, validate=False)
    yield built
    built.close()


@pytest.fixture
def version() -> str:
    return OCPP


@pytest.fixture
def dialect_names() -> dict[str, Any]:
    """The words this version uses, so one test can read naturally in both."""
    if OCPP == V16:
        return {
            "start": "RemoteStartTransaction",
            "stop": "RemoteStopTransaction",
            "session_messages": ("StartTransaction", "MeterValues", "StopTransaction"),
            "tag_field": "idTag",
        }
    return {
        "start": "RequestStartTransaction",
        "stop": "RequestStopTransaction",
        "session_messages": ("TransactionEvent",),
        "tag_field": "idToken",
    }
