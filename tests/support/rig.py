"""Putting a charge point, a back office and a line together for a test.

``rig()`` gives a conversation over the link the run was asked for. ``drive`` is the plain-language
driver a test uses: plug in, present a card, let time pass, cut the line. Both OCPP versions go through
the same calls, so a behaviour test reads the same for 1.6 and 2.0.1.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from evse_lab.csms import Csms
from evse_lab.dialect import Dialect
from evse_lab.link import Link, MemoryLink, WebSocketServer, connect, memory_pair
from evse_lab.node import Conversation, CsmsEndpoint, Exchange, StationEndpoint
from evse_lab.schemas import V16, V201
from evse_lab.station import AuthStatus, ChargePoint, Profile, Reason
from evse_lab.v16 import V16Dialect
from evse_lab.v201 import V201Dialect
from support.evidence import RECORDER

DIALECTS: dict[str, type[Dialect]] = {V16: V16Dialect, V201: V201Dialect}


def dialect_for(version: str) -> Dialect:
    return DIALECTS[version]()


@dataclass
class Rig:
    """One charge point talking to one back office."""

    version: str
    kind: str  # memory | websocket
    station: ChargePoint
    csms: Csms
    talk: Conversation
    closers: list[Any]

    @property
    def cp(self) -> StationEndpoint:
        return self.talk.station

    @property
    def back(self) -> CsmsEndpoint:
        return self.talk.csms

    # ---- the driver a test uses ------------------------------------------------------------------

    def boot(self) -> None:
        """Power on and register with the back office."""
        self.station.boot()
        self.talk.settle()

    def plug_in(self, connector: int = 1) -> None:
        self.station.plug_in(connector)
        self.talk.settle()

    def card(self, id_tag: str = "CARD-OK", connector: int = 1) -> AuthStatus:
        status = self.station.present_card(id_tag, connector)
        self.talk.settle()
        return status

    def car_ready(self, ready: bool = True, connector: int = 1) -> None:
        self.station.car_ready(connector, ready)
        self.talk.settle()

    def unplug(self, connector: int = 1) -> None:
        self.station.unplug(connector)
        self.talk.settle()

    def charge(self, seconds: int, step: int = 0) -> None:
        """Let time pass with the line working."""
        self.talk.run(seconds, step)

    def offline(self) -> None:
        self.station.go_offline()

    def online(self) -> None:
        self.station.go_online()
        self.talk.settle()

    def order(self, action: str, payload: dict[str, Any]) -> Exchange:
        """The back office sends an order and the answer comes back."""
        call = self.back.order(action, payload)
        self.talk.settle()
        for exchange in self.back.history:
            if exchange.call.id == call.id:
                return exchange
        raise AssertionError(f"{action} was never answered")

    def start_session(self, id_tag: str = "CARD-OK", connector: int = 1) -> None:
        """The usual beginning: cable in, card on the reader, car ready."""
        self.plug_in(connector)
        self.card(id_tag, connector)
        self.car_ready(True, connector)

    def profile(self, limit_a: float, connector: int = 1, **kwargs: Any) -> Exchange:
        """Send a charging profile in the words of this version."""
        if self.version == V16:
            return self.order(
                "SetChargingProfile", {"connectorId": connector, "csChargingProfiles": v16_profile(limit_a, **kwargs)}
            )
        return self.order(
            "SetChargingProfile", {"evseId": connector, "chargingProfile": v201_profile(limit_a, **kwargs)}
        )

    def transaction_key(self, connector: int = 1) -> str:
        """The id the back office knows this connector's transaction by."""
        transaction = self.station.connector(connector).transaction
        assert transaction is not None, "no transaction on this connector"
        if self.version == V16:
            assert transaction.remote_id is not None, "the back office has not given a transaction id yet"
            return str(transaction.remote_id)
        return f"TX-{transaction.local_id:06d}"

    def close(self) -> None:
        for closer in self.closers:
            with contextlib.suppress(Exception):
                closer()


def v16_profile(
    limit_a: float,
    profile_id: int = 1,
    stack_level: int = 0,
    duration: int | None = None,
    start: str | None = None,
    purpose: str = "TxDefaultProfile",
    unit: str = "A",
) -> dict[str, Any]:
    schedule: dict[str, Any] = {
        "chargingRateUnit": unit,
        "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit_a}],
    }
    if start is not None:
        schedule["startSchedule"] = start
    if duration is not None:
        schedule["duration"] = duration
    return {
        "chargingProfileId": profile_id,
        "stackLevel": stack_level,
        "chargingProfilePurpose": purpose,
        "chargingProfileKind": "Absolute" if start else "Relative",
        "chargingSchedule": schedule,
    }


def v201_profile(
    limit_a: float,
    profile_id: int = 1,
    stack_level: int = 0,
    duration: int | None = None,
    start: str | None = None,
    purpose: str = "TxDefaultProfile",
    unit: str = "A",
) -> dict[str, Any]:
    schedule: dict[str, Any] = {
        "id": profile_id,
        "chargingRateUnit": unit,
        "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit_a}],
    }
    if start is not None:
        schedule["startSchedule"] = start
    if duration is not None:
        schedule["duration"] = duration
    return {
        "id": profile_id,
        "stackLevel": stack_level,
        "chargingProfilePurpose": purpose,
        "chargingProfileKind": "Absolute" if start else "Relative",
        "chargingSchedule": [schedule],
    }


def build(version: str, kind: str = "memory", validate: bool = True, connectors: int = 2) -> Rig:
    station = ChargePoint(connectors=connectors)
    csms = Csms(version=version)
    closers: list[Any] = []
    cp_link: Link
    back_link: Link
    if kind == "memory":
        cp_link, back_link = memory_pair()
        closers += [cp_link.close, back_link.close]
    else:
        server = WebSocketServer(version=version)
        cp_ws = connect(server.url, version=version)
        back_ws = server.accept()
        cp_link, back_link = cp_ws, back_ws
        closers += [cp_ws.close, back_ws.close, server.close]
    cp_end = StationEndpoint(station, dialect_for(version), cp_link, validate)
    cp_end.record.watcher = RECORDER.frame  # the station's record holds both directions: it is the conversation
    talk = Conversation(cp_end, CsmsEndpoint(csms, back_link, validate), timeout=0.0 if kind == "memory" else 0.02)
    return Rig(version, kind, station, csms, talk, closers)


__all__ = [
    "AuthStatus",
    "MemoryLink",
    "Profile",
    "Reason",
    "Rig",
    "build",
    "dialect_for",
    "v16_profile",
    "v201_profile",
]
