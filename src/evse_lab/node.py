"""The two ends of the conversation, and the rules of talking.

An endpoint sends what its side wants to say, answers what the other side asks, and matches every answer
to the call it belongs to. OCPP 1.6 lets a station have only one call outstanding at a time, so a call is
sent and then the station waits; meanwhile the back office may send an order, and that order is answered
while waiting. Both of those are modelled here.

Every payload is checked against the official JSON schema on the way out and on the way in. A test that
wants to send something the standard forbids turns the check off on purpose.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from evse_lab import rpc, schemas
from evse_lab.csms import Csms
from evse_lab.dialect import Dialect, clock_of
from evse_lab.link import Link, LinkClosed
from evse_lab.rpc import Call, CallError, CallResult, MalformedMessage, Message
from evse_lab.station import ChargePoint


@dataclass
class Exchange:
    """One call and whatever came back."""

    call: Call
    answer: Message | None = None

    @property
    def ok(self) -> bool:
        return isinstance(self.answer, CallResult)

    @property
    def payload(self) -> dict[str, Any]:
        if isinstance(self.answer, CallResult):
            return self.answer.payload
        return {}

    @property
    def error(self) -> CallError | None:
        return self.answer if isinstance(self.answer, CallError) else None


@dataclass
class Record:
    """Every frame that crossed this endpoint, for the report and the visual page.

    ``watcher`` lets a test suite collect the frames as they happen, in the order they happen.
    """

    frames: list[tuple[str, str]] = field(default_factory=list)  # (direction tx|rx, the text on the wire)
    watcher: Callable[[str, str], None] | None = None

    def add(self, direction: str, text: str) -> None:
        self.frames.append((direction, text))
        if self.watcher is not None:
            self.watcher(direction, text)


class Endpoint:
    """One side of an OCPP connection."""

    def __init__(self, link: Link, version: str, validate: bool = True, name: str = "") -> None:
        self.link = link
        self.version = version
        self.validate = validate
        self.name = name
        self.record = Record()
        self.outstanding: Exchange | None = None
        self.pending: list[Call] = []  # calls this side still wants to send
        self.history: list[Exchange] = []
        self.received_errors: list[CallError] = []
        self.sent_errors: list[CallError] = []

    # ---- what this side can do; the subclasses fill these in -----------------------------------

    def supported(self) -> frozenset[str]:
        return frozenset()

    def on_call(self, call: Call) -> dict[str, Any]:
        raise NotImplementedError

    def on_answer(self, exchange: Exchange) -> None:
        pass

    def refresh(self) -> None:
        """Collect anything this side now wants to send."""

    # ---- sending ------------------------------------------------------------------------------

    def queue(self, call: Call) -> None:
        self.pending.append(call)

    def send_raw(self, text: str) -> None:
        """Put text on the wire exactly as given. For tests of malformed input."""
        self.record.add("tx", text)
        self.link.send(text)

    def _send(self, message: Message) -> None:
        if self.validate and isinstance(message, Call):
            schemas.validate(self.version, message.action, message.payload)
        if self.validate and isinstance(message, CallResult) and self.outstanding is not None:
            schemas.validate(self.version, self.outstanding.call.action, message.payload, response=True)
        self.send_raw(rpc.dump(message))

    def send_next(self) -> bool:
        """Send one waiting call, if the previous one has been answered. True if something was sent."""
        self.refresh()
        if self.outstanding is not None or not self.pending:
            return False
        call = self.pending.pop(0)
        if self.validate:
            schemas.validate(self.version, call.action, call.payload)
        self.outstanding = Exchange(call)
        self.send_raw(rpc.dump(call))
        return True

    # ---- receiving ------------------------------------------------------------------------------

    def step(self, timeout: float = 0.2) -> bool:
        """Handle at most one incoming frame. True if one was handled."""
        try:
            text = self.link.receive(timeout)
        except LinkClosed:
            return False
        if text is None:
            return False
        self.record.add("rx", text)
        self._handle_frame(text)
        return True

    def _handle_frame(self, text: str) -> None:
        try:
            message = rpc.parse(text)
        except MalformedMessage as bad:
            if bad.message_id:
                self._refuse(bad.message_id, bad.error_code, str(bad))
            return
        if isinstance(message, Call):
            self._serve(message)
        elif isinstance(message, CallResult):
            self._match(message)
        else:
            self.received_errors.append(message)
            self._match(message)

    def _serve(self, call: Call) -> None:
        if call.action not in self.supported():
            known = call.action in schemas.actions(self.version)
            code = rpc.NOT_IMPLEMENTED if known else rpc.NOT_SUPPORTED
            self._refuse(call.id, code, f"{call.action} is not available on this endpoint")
            return
        try:
            schemas.validate(self.version, call.action, call.payload)
        except schemas.SchemaViolation as bad:
            self._refuse(call.id, rpc.FORMATION_VIOLATION, bad.errors[0])
            return
        try:
            payload = self.on_call(call)
        except (KeyError, ValueError, TypeError) as bad:
            self._refuse(call.id, rpc.PROPERTY_CONSTRAINT_VIOLATION, str(bad))
            return
        except Exception as bad:  # a bug on this side must not take the connection down
            self._refuse(call.id, rpc.INTERNAL_ERROR, str(bad))
            return
        if self.validate:
            schemas.validate(self.version, call.action, payload, response=True)
        self.send_raw(rpc.dump(CallResult(call.id, payload)))

    def _match(self, message: Message) -> None:
        outstanding = self.outstanding
        if outstanding is None or message.id != outstanding.call.id:
            return  # an answer to nothing: ignored, as the standard says
        outstanding.answer = message
        self.outstanding = None
        self.history.append(outstanding)
        if isinstance(message, CallResult) and self.validate:
            schemas.validate(self.version, outstanding.call.action, message.payload, response=True)
        self.on_answer(outstanding)

    def _refuse(self, message_id: str, code: str, description: str) -> None:
        error = CallError(message_id, code, description[:255])
        self.sent_errors.append(error)
        self.send_raw(rpc.dump(error))

    # ---- what a test asks ---------------------------------------------------------------------

    def sent_actions(self) -> list[str]:
        return [exchange.call.action for exchange in self.history]

    def last(self, action: str) -> Exchange:
        for exchange in reversed(self.history):
            if exchange.call.action == action:
                return exchange
        raise AssertionError(f"{self.name or 'this side'} never sent {action}")


class StationEndpoint(Endpoint):
    """The charge point's side: it reports what happens and carries out orders."""

    def __init__(self, station: ChargePoint, dialect: Dialect, link: Link, validate: bool = True) -> None:
        super().__init__(link, dialect.version, validate, name="charge point")
        self.station = station
        self.dialect = dialect
        self.waiting: list[Any] = []  # events not yet turned into messages

    def supported(self) -> frozenset[str]:
        return self.dialect.supported()

    def refresh(self) -> None:
        """Turn the next event into its message, and only the next one.

        A message is built at the moment it is sent, never earlier. After an outage this is what keeps
        OCPP 1.6 honest: the station learns the transaction number from the answer to StartTransaction,
        and the StopTransaction it still owes has to carry that number. A station that had already built
        its queued messages would send the wrong one.
        """
        self.waiting.extend(self.station.take_events())
        while not self.pending and self.waiting:
            # An event may become no message at all: see the duplicate statuses of OCPP 2.0.1.
            self.pending.extend(self.dialect.calls_for(self.station, self.waiting.pop(0)))

    def on_call(self, call: Call) -> dict[str, Any]:
        return self.dialect.handle(self.station, call)

    def on_answer(self, exchange: Exchange) -> None:
        if isinstance(exchange.answer, CallResult):
            self.dialect.on_result(self.station, exchange.call, exchange.answer.payload)


class CsmsEndpoint(Endpoint):
    """The back office's side: it answers the station and may send it orders."""

    def __init__(self, csms: Csms, link: Link, validate: bool = True) -> None:
        super().__init__(link, csms.version, validate, name="back office")
        self.csms = csms
        self.orders: frozenset[str] = frozenset()

    def supported(self) -> frozenset[str]:
        return frozenset(schemas.actions(self.version))

    def on_call(self, call: Call) -> dict[str, Any]:
        stamp = call.payload.get("timestamp")
        if isinstance(stamp, str):
            with contextlib.suppress(ValueError):
                self.csms.clock = clock_of(stamp)
        return self.csms.handle(call)

    def order(self, action: str, payload: dict[str, Any]) -> Call:
        """Queue an order for the charge point."""
        call = Call(f"S{len(self.pending) + len(self.history) + 1:07d}", action, payload)
        self.queue(call)
        return call


class Conversation:
    """Two endpoints joined by a link, driven by hand so that every run is the same.

    ``settle`` lets both sides talk until neither has anything left to say. Nothing runs in the
    background: a test knows exactly when messages move.
    """

    def __init__(self, station: StationEndpoint, csms: CsmsEndpoint, timeout: float = 0.0) -> None:
        self.station = station
        self.csms = csms
        self.timeout = timeout  # 0 for an in-memory link; a real socket needs a moment

    @property
    def ends(self) -> tuple[Endpoint, Endpoint]:
        return self.station, self.csms

    def settle(self, rounds: int = 400, timeout: float | None = None) -> None:
        timeout = self.timeout if timeout is None else timeout
        for _ in range(rounds):
            busy = False
            for end in self.ends:
                if end.step(timeout):
                    busy = True
            for end in self.ends:
                if end.send_next():
                    busy = True
            if not busy:
                # one last read in case the final answer is still on the wire
                if any(end.step(timeout) for end in self.ends):
                    continue
                return
        raise AssertionError("the two sides never stopped talking")

    def run(self, seconds: int, step: int = 0) -> None:
        """Let simulated time pass and let both sides talk, in slices if a step is given."""
        slice_s = step or seconds
        left = seconds
        while left > 0:
            now = min(slice_s, left)
            self.station.station.advance(now)
            left -= now
            self.settle()
