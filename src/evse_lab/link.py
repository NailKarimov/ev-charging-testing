"""The line between the charge point and the back office: in memory, or a real WebSocket.

Every link moves one text frame at a time, the way a WebSocket does, so the same conversation runs
inside one process or across a socket. ``memory_pair`` is the default in tests: it is instant and
repeatable. The WebSocket links prove the same messages survive a real connection, a subprotocol
handshake and a connection that is cut in the middle.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Sequence
from typing import Any, Protocol

SUBPROTOCOLS = {"1.6": "ocpp1.6", "2.0.1": "ocpp2.0.1"}


class LinkClosed(ConnectionError):
    """The other side hung up."""


class Link(Protocol):
    def send(self, text: str) -> None: ...

    def receive(self, timeout: float = 1.0) -> str | None:
        """One frame, or None if none arrived in time."""
        ...

    def close(self) -> None: ...


class MemoryLink:
    """One end of a pair of queues: a perfect connection with no network in between."""

    def __init__(self, inbox: queue.Queue[str | None], outbox: queue.Queue[str | None]) -> None:
        self._in = inbox
        self._out = outbox
        self._closed = False
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        if self._closed:
            raise LinkClosed("the link is closed")
        self.sent.append(text)
        self._out.put(text)

    def receive(self, timeout: float = 1.0) -> str | None:
        try:
            # Both ends live in one thread, so an empty queue means nothing is coming: do not wait.
            frame = self._in.get_nowait() if timeout <= 0 else self._in.get(timeout=timeout)
        except queue.Empty:
            return None
        if frame is None:
            raise LinkClosed("closed by the other side")
        return frame

    def waiting(self) -> int:
        return self._in.qsize()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                self._out.put(None)


def memory_pair() -> tuple[MemoryLink, MemoryLink]:
    a: queue.Queue[str | None] = queue.Queue()
    b: queue.Queue[str | None] = queue.Queue()
    return MemoryLink(a, b), MemoryLink(b, a)


class WebSocketLink:
    """A real WebSocket connection, used through the synchronous interface of ``websockets``."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @property
    def subprotocol(self) -> str | None:
        value = self._connection.protocol.subprotocol
        return str(value) if value is not None else None

    def send(self, text: str) -> None:
        import websockets.exceptions

        try:
            self._connection.send(text)
        except websockets.exceptions.WebSocketException as exc:
            raise LinkClosed(str(exc)) from exc

    def receive(self, timeout: float = 1.0) -> str | None:
        import websockets.exceptions

        try:
            frame = self._connection.recv(timeout=timeout)
        except TimeoutError:
            return None
        except websockets.exceptions.ConnectionClosed as exc:
            raise LinkClosed(str(exc)) from exc
        return frame if isinstance(frame, str) else frame.decode()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._connection.close()


class WebSocketServer:
    """Accepts charge points on 127.0.0.1. Each connection becomes a ``WebSocketLink``."""

    def __init__(self, version: str = "1.6", path: str = "/ocpp") -> None:
        from websockets.sync.server import serve

        self.version = version
        self.path = path
        self.accepted: queue.Queue[WebSocketLink] = queue.Queue()
        self.rejected: list[str] = []
        self._server = serve(
            self._on_connection,
            "127.0.0.1",
            0,
            subprotocols=[SUBPROTOCOLS[version]],  # type: ignore[list-item]
            select_subprotocol=self._select,
            process_request=self._check_subprotocol,
        )
        self.port = int(self._server.socket.getsockname()[1])
        self._thread = threading.Thread(target=self._server.serve_forever, name="ocpp-server", daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}{self.path}"

    def _check_subprotocol(self, connection: Any, request: Any) -> Any:
        """Turn a station away at the handshake when it does not speak this version.

        OCPP-J says the central system refuses the connection when it does not support the subprotocol
        the station asks for. Letting the connection open with no subprotocol agreed is worse than
        refusing: both sides then speak past each other and only find out message by message.
        """
        asked = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
        if SUBPROTOCOLS[self.version] in asked:
            return None
        self.rejected.append(",".join(asked))
        return connection.respond(400, "this central system speaks only " + SUBPROTOCOLS[self.version] + "\n")

    def _select(self, connection: Any, subprotocols: Sequence[Any]) -> Any:
        wanted = SUBPROTOCOLS[self.version]
        return wanted if wanted in subprotocols else None

    def _on_connection(self, connection: Any) -> None:
        link = WebSocketLink(connection)
        self.accepted.put(link)
        # Hold the connection open; whoever took the link decides when it ends.
        with contextlib.suppress(Exception):
            connection.wait_closed()

    def accept(self, timeout: float = 5.0) -> WebSocketLink:
        try:
            return self.accepted.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("no charge point connected in time") from exc

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._server.shutdown()


def connect(url: str, version: str = "1.6", timeout: float = 5.0) -> WebSocketLink:
    """Connect as a charge point, asking for the subprotocol of this OCPP version."""
    from websockets.sync.client import connect as ws_connect

    connection = ws_connect(url, subprotocols=[SUBPROTOCOLS[version]], open_timeout=timeout)  # type: ignore[list-item]
    return WebSocketLink(connection)
