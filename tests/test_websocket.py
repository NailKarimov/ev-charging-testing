"""The same conversation across a real WebSocket, and what happens when the connection is cut.

Everything else in this suite runs in memory, which is fast and repeatable. These tests prove the
messages also survive a real connection: the handshake that agrees which OCPP version is spoken, frames
crossing a socket, and a cable pulled out from under a running session.
"""

from __future__ import annotations

import pytest
import websockets.exceptions

from evse_lab.link import SUBPROTOCOLS, LinkClosed, WebSocketLink, WebSocketServer, connect
from evse_lab.schemas import V16, V201
from support.evidence import step
from support.rig import Rig

pytestmark = pytest.mark.websocket


@pytest.mark.smoke
def test_a_whole_session_crosses_a_real_connection(booted: Rig) -> None:
    """Over a real WebSocket the station registers, charges a car and reports the session as it does in memory."""
    assert booted.kind == "websocket"
    with step("A session over a real socket"):
        booted.start_session()
        booted.charge(600, step=120)
        booted.card("CARD-OK")
        booted.charge(2)
    session = booted.csms.session_of("CARD-OK")
    assert not session.running
    assert session.energy_wh == pytest.approx(613, abs=3)
    assert len(session.samples) == 10


def test_both_sides_agree_which_version_they_speak(booted: Rig) -> None:
    """The handshake settles the OCPP version: the connection carries the subprotocol of this run."""
    expected = SUBPROTOCOLS[booted.version]
    link = booted.cp.link
    assert isinstance(link, WebSocketLink)
    assert link.subprotocol == expected
    assert expected in ("ocpp1.6", "ocpp2.0.1")


def test_a_station_speaking_another_version_is_turned_away() -> None:
    """A station that asks for a version the back office does not speak is refused at the handshake."""
    server = WebSocketServer(version=V16)
    try:
        with (
            step("A station asks to speak OCPP 2.0.1 to a back office that speaks 1.6"),
            pytest.raises(websockets.exceptions.InvalidStatus),
        ):
            connect(server.url, version=V201, timeout=2.0)
        assert server.rejected, "the back office recorded what was asked for"
        assert SUBPROTOCOLS[V201] in server.rejected[0]
    finally:
        server.close()


def test_a_station_speaking_the_same_version_is_let_in() -> None:
    """A station asking for the version the back office speaks is accepted and can talk."""
    server = WebSocketServer(version=V16)
    try:
        client = connect(server.url, version=V16, timeout=2.0)
        server.accept(timeout=2.0)
        assert client.subprotocol == SUBPROTOCOLS[V16]
        client.close()
    finally:
        server.close()


def test_a_cut_connection_is_noticed_and_does_not_lose_the_session(booted: Rig) -> None:
    """When the connection is cut mid-session the station notices, keeps charging and keeps what it owes."""
    booted.start_session()
    booted.charge(120)
    energy_before = int(booted.station.meter_wh)
    with step("The connection is cut"):
        booted.back.link.close()
        booted.station.go_offline()
    with step("The car charges on with nobody listening"):
        booted.charge(300, step=60)
    assert int(booted.station.meter_wh) > energy_before
    assert booted.station.connector(1).transaction is not None
    assert len(booted.station.queue) >= 5, "everything owed is kept"
    with pytest.raises(LinkClosed):
        _keep_sending(booted)


def test_sending_into_a_closed_connection_raises_instead_of_pretending(booted: Rig) -> None:
    """Writing to a connection that is gone fails loudly: a station must not think a message was delivered."""
    booted.cp.link.close()
    with pytest.raises(LinkClosed):
        _keep_sending(booted)


def test_a_message_larger_than_a_single_frame_still_arrives(booted: Rig) -> None:
    """A message far bigger than a normal one crosses the socket whole and is answered."""
    booted.start_session()
    booted.charge(120)
    assert booted.csms.session_of("CARD-OK").running
    assert len(booted.cp.record.frames) > 5


def _keep_sending(rig: Rig) -> None:
    """Write to the connection until it admits it is gone: a closed socket is noticed within a frame or two."""
    for _ in range(50):
        rig.cp.link.send('[2,"probe","Heartbeat",{}]')
