"""The back office: the central system a charge point reports to.

It answers the station's messages and keeps what it has been told, so a test can ask it plain questions:
how many sessions does it know, how much energy did this one deliver, in what order did the messages
arrive. That record is what a charge point operator bills from, which is why "nothing lost, nothing
doubled" is the thing worth proving.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from evse_lab.dialect import iso
from evse_lab.rpc import Call
from evse_lab.schemas import V16, V201
from evse_lab.station import AuthStatus

BOOT_INTERVAL = 300


@dataclass
class Session:
    """One charging session as the back office knows it."""

    key: str  # the transaction id: a number in 1.6, text in 2.0.1
    connector: int
    id_tag: str
    meter_start: int
    started_at: str
    meter_stop: int | None = None
    stopped_at: str | None = None
    reason: str | None = None
    samples: list[int] = field(default_factory=list)
    seq_nos: list[int] = field(default_factory=list)
    offline_events: int = 0

    @property
    def running(self) -> bool:
        return self.meter_stop is None

    @property
    def energy_wh(self) -> int:
        return 0 if self.meter_stop is None else self.meter_stop - self.meter_start


class Csms:
    def __init__(self, version: str = V16, tags: dict[str, AuthStatus] | None = None) -> None:
        self.version = version
        self.tags = dict(
            tags
            or {
                "CARD-OK": AuthStatus.ACCEPTED,
                "CARD-OK-2": AuthStatus.ACCEPTED,
                "CARD-BLOCKED": AuthStatus.BLOCKED,
                "CARD-EXPIRED": AuthStatus.EXPIRED,
            }
        )
        self.boot_status = "Accepted"
        self.boot_interval = BOOT_INTERVAL
        self.sessions: dict[str, Session] = {}
        self.received: list[Call] = []
        self.statuses: list[dict[str, Any]] = []
        self.boots = 0
        self.heartbeats = 0
        self.clock = 0  # set by whoever drives the conversation, for the timestamps it sends back
        self._numbers = itertools.count(1001)

    # ---- the station speaks ---------------------------------------------------------------------

    def handle(self, call: Call) -> dict[str, Any]:
        self.received.append(call)
        action = call.action
        handler = getattr(self, f"_on_{_snake(action)}", None)
        if handler is None:
            raise KeyError(action)
        result: dict[str, Any] = handler(call.payload)
        return result

    def _on_boot_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.boots += 1
        return {"currentTime": iso(self.clock), "interval": self.boot_interval, "status": self.boot_status}

    def _on_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.heartbeats += 1
        return {"currentTime": iso(self.clock)}

    def _on_status_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.statuses.append(payload)
        return {}

    def _on_authorize(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.version == V16:
            return {"idTagInfo": self._tag_info(str(payload["idTag"]))}
        return {"idTokenInfo": self._token_info(str(payload["idToken"]["idToken"]))}

    # -- OCPP 1.6 sessions ---------------------------------------------------------------------------

    def _on_start_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        number = next(self._numbers)
        self.sessions[str(number)] = Session(
            key=str(number),
            connector=int(payload["connectorId"]),
            id_tag=str(payload["idTag"]),
            meter_start=int(payload["meterStart"]),
            started_at=str(payload["timestamp"]),
        )
        return {"transactionId": number, "idTagInfo": self._tag_info(str(payload["idTag"]))}

    def _on_meter_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = str(payload.get("transactionId", ""))
        session = self.sessions.get(key)
        if session is not None:
            for entry in payload.get("meterValue", []):
                for sample in entry.get("sampledValue", []):
                    if sample.get("measurand") == "Energy.Active.Import.Register":
                        session.samples.append(int(float(sample["value"])))
        return {}

    def _on_stop_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = str(payload["transactionId"])
        session = self.sessions.get(key)
        if session is not None:
            session.meter_stop = int(payload["meterStop"])
            session.stopped_at = str(payload["timestamp"])
            session.reason = payload.get("reason")
        answer: dict[str, Any] = {}
        if "idTag" in payload:
            answer["idTagInfo"] = self._tag_info(str(payload["idTag"]))
        return answer

    # -- OCPP 2.0.1 sessions -------------------------------------------------------------------------

    def _on_transaction_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        info = payload["transactionInfo"]
        key = str(info["transactionId"])
        kind = payload["eventType"]
        energy = _energy_of(payload)
        if kind == "Started":
            self.sessions[key] = Session(
                key=key,
                connector=int(payload.get("evse", {}).get("id", 1)),
                id_tag=str(payload.get("idToken", {}).get("idToken", "")),
                meter_start=energy or 0,
                started_at=str(payload["timestamp"]),
            )
        session = self.sessions.get(key)
        if session is not None:
            session.seq_nos.append(int(payload["seqNo"]))
            if payload.get("offline"):
                session.offline_events += 1
            if kind == "Updated" and energy is not None:
                session.samples.append(energy)
            if kind == "Ended":
                session.meter_stop = energy
                session.stopped_at = str(payload["timestamp"])
                session.reason = info.get("stoppedReason")
        answer: dict[str, Any] = {}
        if kind == "Started" and "idToken" in payload:
            answer["idTokenInfo"] = self._token_info(str(payload["idToken"]["idToken"]))
        return answer

    # ---- what a test asks it --------------------------------------------------------------------

    def session_of(self, id_tag: str) -> Session:
        found = [s for s in self.sessions.values() if s.id_tag == id_tag]
        if len(found) != 1:
            raise AssertionError(f"expected exactly one session for {id_tag!r}, the back office knows {len(found)}")
        return found[0]

    def actions(self) -> list[str]:
        return [call.action for call in self.received]

    def payloads(self, action: str) -> list[dict[str, Any]]:
        return [call.payload for call in self.received if call.action == action]

    def _tag_info(self, id_tag: str) -> dict[str, Any]:
        return {"status": self.tags.get(id_tag, AuthStatus.INVALID).value}

    def _token_info(self, id_tag: str) -> dict[str, Any]:
        status = self.tags.get(id_tag, AuthStatus.INVALID)
        # 2.0.1 renamed two of the statuses.
        name = {AuthStatus.INVALID: "Unknown", AuthStatus.CONCURRENT_TX: "ConcurrentTx"}.get(status, status.value)
        return {"status": name}


def _snake(action: str) -> str:
    out = []
    for index, letter in enumerate(action):
        if letter.isupper() and index:
            out.append("_")
        out.append(letter.lower())
    return "".join(out)


def _energy_of(payload: dict[str, Any]) -> int | None:
    for entry in payload.get("meterValue", []):
        for sample in entry.get("sampledValue", []):
            if sample.get("measurand") == "Energy.Active.Import.Register":
                return int(float(sample["value"]))
    return None


__all__ = ["Csms", "Session", "V16", "V201"]
