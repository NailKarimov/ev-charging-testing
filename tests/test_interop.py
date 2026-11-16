"""Talking to software written by other people.

The ``ocpp`` library from The Mobility House is the reference implementation most of this industry
tests against. Here it sits at the other end of a real WebSocket: first as the back office answering
this project's charge point, then as a charge point talking to this project's back office. If this
project had quietly invented its own dialect, the library would not understand it.

The library is asynchronous, so it runs in its own thread with its own event loop, as it would in a
real back office.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any

import pytest
from websockets.typing import Subprotocol

from evse_lab.link import SUBPROTOCOLS, WebSocketServer, connect
from evse_lab.schemas import V16
from support.evidence import step
from support.rig import Rig

pytestmark = pytest.mark.interop


class LibraryThread:
    """Runs an asyncio application from the ``ocpp`` library beside the test."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="ocpp-library", daemon=True)
        self.thread.start()

    def run(self, coroutine: Any, timeout: float = 10.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def spawn(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        with contextlib.suppress(Exception):
            self.loop.close()


@pytest.fixture
def library() -> Any:
    runner = LibraryThread()
    yield runner
    runner.close()


def _library_csms(version: str, port_holder: dict[str, Any]) -> Any:
    """A back office built from the library, ready to answer one charge point."""
    import websockets
    from ocpp.routing import on

    if version == V16:
        from ocpp.v16 import ChargePoint as LibraryCp
        from ocpp.v16 import call_result
        from ocpp.v16.enums import Action, AuthorizationStatus, RegistrationStatus

        class Handler(LibraryCp):  # type: ignore[misc]
            seen: list[str] = []

            @on(Action.boot_notification)
            def on_boot(self, **kwargs: Any) -> Any:
                Handler.seen.append("BootNotification")
                return call_result.BootNotification(
                    current_time="2026-06-15T08:00:00Z", interval=300, status=RegistrationStatus.accepted
                )

            @on(Action.authorize)
            def on_authorize(self, id_tag: str, **kwargs: Any) -> Any:
                Handler.seen.append("Authorize")
                return call_result.Authorize(id_tag_info={"status": AuthorizationStatus.accepted})

            @on(Action.status_notification)
            def on_status(self, **kwargs: Any) -> Any:
                Handler.seen.append("StatusNotification")
                return call_result.StatusNotification()

            @on(Action.start_transaction)
            def on_start(self, **kwargs: Any) -> Any:
                Handler.seen.append("StartTransaction")
                return call_result.StartTransaction(
                    transaction_id=7001, id_tag_info={"status": AuthorizationStatus.accepted}
                )

            @on(Action.meter_values)
            def on_meter(self, **kwargs: Any) -> Any:
                Handler.seen.append("MeterValues")
                return call_result.MeterValues()

            @on(Action.stop_transaction)
            def on_stop(self, **kwargs: Any) -> Any:
                Handler.seen.append("StopTransaction")
                return call_result.StopTransaction(id_tag_info={"status": AuthorizationStatus.accepted})

            @on(Action.heartbeat)
            def on_heartbeat(self, **kwargs: Any) -> Any:
                Handler.seen.append("Heartbeat")
                return call_result.Heartbeat(current_time="2026-06-15T08:00:00Z")
    else:
        from ocpp.v201 import ChargePoint as LibraryCp
        from ocpp.v201 import call_result
        from ocpp.v201.enums import Action, AuthorizationStatusEnumType, RegistrationStatusEnumType

        class Handler(LibraryCp):  # type: ignore[misc, no-redef]
            seen: list[str] = []

            @on(Action.boot_notification)
            def on_boot(self, **kwargs: Any) -> Any:
                Handler.seen.append("BootNotification")
                return call_result.BootNotification(
                    current_time="2026-06-15T08:00:00Z", interval=300, status=RegistrationStatusEnumType.accepted
                )

            @on(Action.authorize)
            def on_authorize(self, **kwargs: Any) -> Any:
                Handler.seen.append("Authorize")
                return call_result.Authorize(id_token_info={"status": AuthorizationStatusEnumType.accepted})

            @on(Action.status_notification)
            def on_status(self, **kwargs: Any) -> Any:
                Handler.seen.append("StatusNotification")
                return call_result.StatusNotification()

            @on(Action.transaction_event)
            def on_transaction(self, **kwargs: Any) -> Any:
                Handler.seen.append("TransactionEvent")
                return call_result.TransactionEvent()

            @on(Action.heartbeat)
            def on_heartbeat(self, **kwargs: Any) -> Any:
                Handler.seen.append("Heartbeat")
                return call_result.Heartbeat(current_time="2026-06-15T08:00:00Z")

    Handler.seen = []
    port_holder["handler"] = Handler

    async def serve() -> Any:
        async def on_connect(connection: Any) -> None:
            point = Handler("SIM-EVSE-0001", connection)
            port_holder["point"] = point
            with contextlib.suppress(Exception):
                await point.start()

        server = await websockets.serve(on_connect, "127.0.0.1", 0, subprotocols=[Subprotocol(SUBPROTOCOLS[version])])
        port_holder["port"] = server.sockets[0].getsockname()[1]
        return server

    return serve()


@pytest.mark.smoke
def test_the_reference_library_understands_a_whole_session(library: Any, version: str) -> None:
    """A back office built from the reference library follows a full session from this project's station."""
    holder: dict[str, Any] = {}
    server = library.run(_library_csms(version, holder))
    try:
        link = connect(f"ws://127.0.0.1:{holder['port']}/SIM-EVSE-0001", version=version)
        rig = _rig_on(link, version)
        with step("The station registers with the library's back office"):
            rig.boot()
        with step("A driver charges for ten minutes"):
            rig.start_session()
            rig.charge(600, step=120)
            rig.card("CARD-OK")
            rig.charge(2)
        seen = holder["handler"].seen
        assert "BootNotification" in seen
        assert "Authorize" in seen
        if version == V16:
            assert "StartTransaction" in seen
            assert "StopTransaction" in seen
            assert seen.count("MeterValues") >= 5
        else:
            assert seen.count("TransactionEvent") >= 7
        rig.close()
    finally:
        server.close()


def test_the_library_gives_this_station_its_transaction_number(library: Any, version: str) -> None:
    """The transaction number the library hands out is the one this station then uses to end the session."""
    if version != V16:
        pytest.skip("only OCPP 1.6 has the back office hand out the number")
    holder: dict[str, Any] = {}
    server = library.run(_library_csms(version, holder))
    try:
        rig = _rig_on(connect(f"ws://127.0.0.1:{holder['port']}/cp", version=version), version)
        rig.boot()
        rig.start_session()
        rig.charge(120)
        transaction = rig.station.connector(1).transaction
        assert transaction is not None
        assert transaction.remote_id == 7001, "the number came from the library"
        rig.card("CARD-OK")
        rig.charge(2)
        stop = rig.cp.last("StopTransaction")
        assert stop.call.payload["transactionId"] == 7001
        rig.close()
    finally:
        server.close()


def test_the_library_as_a_charge_point_is_understood_by_this_back_office(library: Any, version: str) -> None:
    """A charge point built from the reference library registers and authorises against this project's back office."""
    server = WebSocketServer(version=version)
    try:
        if version == V16:
            from ocpp.v16 import ChargePoint as LibraryCp
            from ocpp.v16 import call
        else:
            from ocpp.v201 import ChargePoint as LibraryCp
            from ocpp.v201 import call

        import websockets

        results: dict[str, Any] = {}

        async def talk() -> None:
            async with websockets.connect(server.url, subprotocols=[Subprotocol(SUBPROTOCOLS[version])]) as connection:
                point = LibraryCp("LIB-CP-1", connection)
                task = asyncio.ensure_future(point.start())
                if version == V16:
                    boot = await point.call(call.BootNotification(charge_point_model="LIB", charge_point_vendor="Lib"))
                    auth = await point.call(call.Authorize(id_tag="CARD-OK"))
                    results["auth"] = auth.id_tag_info["status"]
                else:
                    boot = await point.call(
                        call.BootNotification(charging_station={"model": "LIB", "vendor_name": "Lib"}, reason="PowerUp")
                    )
                    auth = await point.call(call.Authorize(id_token={"id_token": "CARD-OK", "type": "ISO14443"}))
                    results["auth"] = auth.id_token_info["status"]
                results["boot"] = boot.status
                task.cancel()

        future = library.spawn(talk())  # the library connects first
        back = _back_office_on(server, version)
        for _ in range(400):
            back.step(0.02)
            back.send_next()
            if future.done():
                break
        future.result(timeout=10)
        assert results["boot"] == "Accepted"
        assert results["auth"] == "Accepted"
        assert back.csms.boots == 1
        assert "Authorize" in back.csms.actions()
    finally:
        server.close()


def _rig_on(link: Any, version: str) -> Rig:
    """This project's station on a connection that is already open."""
    from evse_lab.csms import Csms
    from evse_lab.link import memory_pair
    from evse_lab.node import Conversation, CsmsEndpoint, StationEndpoint
    from evse_lab.station import ChargePoint
    from support.evidence import RECORDER
    from support.rig import dialect_for

    station = ChargePoint(connectors=2)
    endpoint = StationEndpoint(station, dialect_for(version), link, validate=True)
    endpoint.record.watcher = RECORDER.frame
    unused_a, unused_b = memory_pair()
    talk = Conversation(endpoint, CsmsEndpoint(Csms(version=version), unused_b), timeout=0.02)

    class Solo(Conversation):  # the other end is the library, not an endpoint of ours
        def settle(self, rounds: int = 400, timeout: float | None = None) -> None:
            for _ in range(rounds):
                busy = endpoint.step(0.02)
                if endpoint.send_next():
                    busy = True
                if not busy and endpoint.outstanding is None:
                    return

    solo = Solo(endpoint, talk.csms, timeout=0.02)
    return Rig(version, "websocket", station, Csms(version=version), solo, [link.close, unused_a.close, unused_b.close])


def _back_office_on(server: WebSocketServer, version: str) -> Any:
    from evse_lab.csms import Csms
    from evse_lab.node import CsmsEndpoint

    link = server.accept(timeout=5.0)
    return CsmsEndpoint(Csms(version=version), link, validate=True)
