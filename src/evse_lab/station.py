"""The charge point itself: the pilot wire, the connectors, the meter and the rules it follows.

Nothing here knows about OCPP. The station reports what happens as ``Event`` objects and takes orders
as ``Command`` calls; ``v16.py`` and ``v201.py`` turn those into the messages of their version. That is
why one station can speak both versions and why a test of the behaviour reads the same for both.

Simulated time moves only when ``advance`` is called, so every run sees the same session.

What is modelled:

- the **pilot wire** of IEC 61851-1: state A no vehicle, B vehicle connected, C vehicle ready to charge,
  E error. The station offers a current by the duty cycle of the pilot signal; the car takes up to that.
- a **transaction**: it starts when a driver is authorised and the cable is in, and it ends on a card, a
  remote command, or the cable being pulled out.
- the **meter**: watt-hours that only grow, sampled at a fixed interval.
- a **charging profile** from the operator: a current limit with a validity window and a stack level.
- **offline**: when the link to the back office is down the station keeps charging and keeps every
  message it owes in a queue, in order. Nothing is invented and nothing is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

NOMINAL_V = 230.0
EVSE_MAX_A = 32.0  # what this station's hardware can give
CABLE_MAX_A = 32.0  # what the cable reports through its resistor
MIN_CHARGE_A = 6.0  # below 6 A the pilot cannot ask a car to charge (IEC 61851-1)
METER_SAMPLE_S = 60  # how often a meter reading is reported during a transaction
BOOT_RETRY_S = 10
START_ENERGY_WH = 4_500_000  # the meter did not start at zero: this station has been in service


class Pilot(StrEnum):
    """Control Pilot states of IEC 61851-1, as the station reads them."""

    A = "A"  # no vehicle
    B = "B"  # vehicle connected, not ready
    C = "C"  # vehicle ready to charge
    E = "E"  # error on the pilot wire


class Status(StrEnum):
    """The connector status OCPP asks for. The names are the same in 1.6 and 2.0.1."""

    AVAILABLE = "Available"
    PREPARING = "Preparing"
    CHARGING = "Charging"
    SUSPENDED_EVSE = "SuspendedEVSE"
    SUSPENDED_EV = "SuspendedEV"
    FINISHING = "Finishing"
    RESERVED = "Reserved"
    UNAVAILABLE = "Unavailable"
    FAULTED = "Faulted"


class Reason(StrEnum):
    """Why a transaction ended. Both versions know these; 2.0.1 spells two of them differently."""

    LOCAL = "Local"
    REMOTE = "Remote"
    EV_DISCONNECTED = "EVDisconnected"
    POWER_LOSS = "PowerLoss"
    DE_AUTHORIZED = "DeAuthorized"
    OTHER = "Other"


class AuthStatus(StrEnum):
    ACCEPTED = "Accepted"
    BLOCKED = "Blocked"
    EXPIRED = "Expired"
    INVALID = "Invalid"
    CONCURRENT_TX = "ConcurrentTx"


# ---- what the station reports ---------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """Something the back office has to be told about."""

    kind: str  # boot | status | started | sample | stopped | heartbeat | authorize
    connector: int = 0
    at: int = 0  # seconds on the simulated clock
    status: Status | None = None
    transaction: Transaction | None = None
    meter_wh: int = 0
    reason: Reason | None = None
    id_tag: str = ""
    offline: bool = False  # it happened while the back office could not be reached
    forced: bool = False  # the back office asked for this message, so it is sent even if nothing changed


@dataclass
class Transaction:
    local_id: int  # the station's own number, known before the back office answers
    connector: int
    id_tag: str
    meter_start: int
    started_at: int
    remote_id: int | None = None  # the number the back office gives it
    meter_stop: int | None = None
    stopped_at: int | None = None
    reason: Reason | None = None
    seq_no: int = 0  # OCPP 2.0.1 counts the events of a transaction
    sampled: int = 0  # how many meter readings were reported

    @property
    def running(self) -> bool:
        return self.stopped_at is None

    @property
    def energy_wh(self) -> int:
        return (self.meter_stop if self.meter_stop is not None else self.meter_start) - self.meter_start


@dataclass
class Profile:
    """A charging profile from the operator: how much current this connector may take."""

    id: int
    limit_a: float
    stack_level: int = 0
    valid_from: int | None = None
    valid_to: int | None = None
    purpose: str = "TxDefaultProfile"

    def active(self, now: int) -> bool:
        if self.valid_from is not None and now < self.valid_from:
            return False
        return not (self.valid_to is not None and now >= self.valid_to)


@dataclass
class Connector:
    id: int
    pilot: Pilot = Pilot.A
    status: Status = Status.AVAILABLE
    operative: bool = True
    transaction: Transaction | None = None
    profiles: dict[int, Profile] = field(default_factory=dict)
    car_max_a: float = 16.0
    plugged_tag: str = ""  # the card presented at this connector, if any
    locked: bool = False

    def limit_a(self, now: int) -> float:
        """The current the station may offer: the hardware, the cable and the strictest active profile."""
        allowed = min(EVSE_MAX_A, CABLE_MAX_A)
        active = [p for p in self.profiles.values() if p.active(now)]
        if active:
            top = max(p.stack_level for p in active)
            allowed = min(allowed, min(p.limit_a for p in active if p.stack_level == top))
        return allowed

    def offered_a(self, now: int) -> float:
        """What the pilot signal offers the car. Below 6 A a car may not charge at all, so it is 0."""
        limit = self.limit_a(now)
        return 0.0 if limit < MIN_CHARGE_A else limit

    def drawn_a(self, now: int) -> float:
        if self.pilot is not Pilot.C or self.transaction is None or not self.transaction.running:
            return 0.0
        return min(self.offered_a(now), self.car_max_a)


class ChargePoint:
    """One charge point with one or more connectors."""

    def __init__(self, connectors: int = 2, serial: str = "SIM-EVSE-0001") -> None:
        self.serial = serial
        self.vendor = "QA Portfolio Lab"
        self.model = "SIM-AC-22"
        self.firmware = "1.0.0"
        self.clock = 8 * 3600  # 08:00 on the simulated day
        self.connectors = {n: Connector(n) for n in range(1, connectors + 1)}
        self.meter_wh = float(START_ENERGY_WH)
        self.booted = False
        self.boot_interval = 300
        self.online = True
        self.events: list[Event] = []  # produced now, to be sent now
        self.queue: list[Event] = []  # produced while offline, to be sent in order later
        self.next_local_id = 1
        self.last_heartbeat = 0
        self.last_sample: dict[int, int] = {}
        self.known_tags: dict[str, AuthStatus] = {
            "CARD-OK": AuthStatus.ACCEPTED,
            "CARD-OK-2": AuthStatus.ACCEPTED,
            "CARD-BLOCKED": AuthStatus.BLOCKED,
            "CARD-EXPIRED": AuthStatus.EXPIRED,
        }
        self.auth_cache: dict[str, AuthStatus] = {}  # what the back office said, used while offline
        self.reset_pending: str | None = None

    # ---- what the driver and the car do -------------------------------------------------------------

    def connector(self, number: int) -> Connector:
        if number not in self.connectors:
            raise KeyError(f"this station has no connector {number}")
        return self.connectors[number]

    def plug_in(self, connector: int = 1) -> None:
        """The cable goes into the car: the pilot moves from A to B."""
        c = self.connector(connector)
        c.pilot = Pilot.B
        c.locked = True
        self._settle(c)

    def car_ready(self, connector: int = 1, ready: bool = True) -> None:
        """The car says it is ready to take power (pilot C) or not any more (pilot B)."""
        c = self.connector(connector)
        if c.pilot is not Pilot.A:
            c.pilot = Pilot.C if ready else Pilot.B
        self._settle(c)

    def unplug(self, connector: int = 1) -> None:
        """The cable comes out. A transaction that is still running ends with EVDisconnected."""
        c = self.connector(connector)
        if c.transaction is not None and c.transaction.running:
            self.stop_transaction(connector, Reason.EV_DISCONNECTED)
        c.pilot = Pilot.A
        c.locked = False
        c.plugged_tag = ""
        self._settle(c)

    def present_card(self, id_tag: str, connector: int = 1) -> AuthStatus:
        """A driver holds a card to the reader. Starts a transaction, or stops the one that card started."""
        c = self.connector(connector)
        if c.transaction is not None and c.transaction.running:
            if c.transaction.id_tag == id_tag:
                self.stop_transaction(connector, Reason.LOCAL)
                return AuthStatus.ACCEPTED
            return AuthStatus.CONCURRENT_TX
        status = self.authorize(id_tag)
        if status is AuthStatus.ACCEPTED:
            self.start_transaction(id_tag, connector)
        return status

    def authorize(self, id_tag: str) -> AuthStatus:
        """Ask the back office, or decide from the cache when the link is down."""
        if self.online:
            status = self.known_tags.get(id_tag, AuthStatus.INVALID)
            self.auth_cache[id_tag] = status
            self.events.append(Event("authorize", at=self.clock, id_tag=id_tag))
            return status
        return self.auth_cache.get(id_tag, AuthStatus.INVALID)

    # ---- transactions ---------------------------------------------------------------------------

    def start_transaction(self, id_tag: str, connector: int = 1) -> Transaction:
        c = self.connector(connector)
        if c.transaction is not None and c.transaction.running:
            raise RuntimeError(f"connector {connector} already has a running transaction")
        transaction = Transaction(
            local_id=self.next_local_id,
            connector=connector,
            id_tag=id_tag,
            meter_start=int(self.meter_wh),
            started_at=self.clock,
        )
        self.next_local_id += 1
        c.transaction = transaction
        c.plugged_tag = id_tag
        self.last_sample[connector] = self.clock
        self._report(Event("started", connector, self.clock, transaction=transaction, id_tag=id_tag))
        self._settle(c)
        return transaction

    def stop_transaction(self, connector: int = 1, reason: Reason = Reason.LOCAL) -> Transaction | None:
        c = self.connector(connector)
        transaction = c.transaction
        if transaction is None or not transaction.running:
            return None
        transaction.meter_stop = int(self.meter_wh)
        transaction.stopped_at = self.clock
        transaction.reason = reason
        self._report(Event("stopped", connector, self.clock, transaction=transaction, reason=reason))
        c.plugged_tag = ""
        self._settle(c)
        return transaction

    # ---- orders from the back office -----------------------------------------------------------------

    def set_profile(self, connector: int, profile: Profile) -> None:
        self.connector(connector).profiles[profile.id] = profile
        self._settle(self.connector(connector))

    def clear_profiles(self, connector: int | None = None, profile_id: int | None = None) -> int:
        """Remove profiles; returns how many were removed."""
        removed = 0
        for number, c in self.connectors.items():
            if connector not in (None, number):
                continue
            for key in [k for k in c.profiles if profile_id in (None, k)]:
                del c.profiles[key]
                removed += 1
            self._settle(c)
        return removed

    def set_operative(self, connector: int, operative: bool) -> None:
        """ChangeAvailability. A connector in use goes to Unavailable only when its transaction ends."""
        if connector == 0:
            for number in self.connectors:
                self.set_operative(number, operative)
            return
        c = self.connector(connector)
        c.operative = operative
        self._settle(c)

    def unlock(self, connector: int) -> str:
        """UnlockConnector: refuses while a transaction is running, as the standard requires."""
        c = self.connector(connector)
        if c.transaction is not None and c.transaction.running:
            return "UnlockFailed"
        if not c.locked:
            return "NotSupported" if c.pilot is Pilot.A else "Unlocked"
        c.locked = False
        return "Unlocked"

    def request_reset(self, kind: str = "Soft") -> None:
        self.reset_pending = kind

    def do_reset(self) -> None:
        """Carry out a pending reset: a hard reset ends a running transaction with PowerLoss."""
        kind, self.reset_pending = self.reset_pending, None
        for number, c in self.connectors.items():
            if c.transaction is not None and c.transaction.running:
                self.stop_transaction(number, Reason.POWER_LOSS if kind == "Hard" else Reason.LOCAL)
        self.booted = False
        for c in self.connectors.values():
            c.pilot = Pilot.A if kind == "Hard" else c.pilot
            c.locked = False
            c.status = Status.AVAILABLE
            c.transaction = None

    # ---- the link to the back office -------------------------------------------------------------

    def go_offline(self) -> None:
        self.online = False

    def go_online(self) -> None:
        """The link is back: everything kept while it was down is handed over, oldest first."""
        self.online = True
        self.events.extend(self.queue)
        self.queue.clear()

    def take_events(self) -> list[Event]:
        """Everything the station wants to send right now."""
        out, self.events = self.events, []
        return out

    def _report(self, event: Event) -> None:
        if self.online:
            self.events.append(event)
        else:
            self.queue.append(replace(event, offline=True))

    # ---- time -------------------------------------------------------------------------------------

    def advance(self, seconds: int) -> None:
        for _ in range(seconds):
            self._tick()

    def _tick(self) -> None:
        self.clock += 1
        for number, c in self.connectors.items():
            drawn = c.drawn_a(self.clock)
            if drawn > 0:
                self.meter_wh += NOMINAL_V * drawn / 3600.0
            transaction = c.transaction
            if transaction is not None and transaction.running:
                due = self.last_sample.get(number, self.clock) + METER_SAMPLE_S
                if self.clock >= due:
                    self.last_sample[number] = self.clock
                    transaction.sampled += 1
                    self._report(
                        Event("sample", number, self.clock, transaction=transaction, meter_wh=int(self.meter_wh))
                    )
            self._settle(c)
        if self.booted and self.clock - self.last_heartbeat >= self.boot_interval:
            self.last_heartbeat = self.clock
            self._report(Event("heartbeat", at=self.clock))

    # ---- the connector status, worked out from the pilot and the transaction ---------------------------

    def _settle(self, c: Connector) -> None:
        status = self._status_of(c)
        if status is not c.status:
            c.status = status
            self._report(Event("status", c.id, self.clock, status=status))

    def _status_of(self, c: Connector) -> Status:
        running = c.transaction is not None and c.transaction.running
        if c.pilot is Pilot.E:
            return Status.FAULTED
        if not c.operative and not running:
            return Status.UNAVAILABLE
        if running:
            if c.pilot is not Pilot.C:
                return Status.SUSPENDED_EV  # the car is not taking power
            if c.offered_a(self.clock) <= 0:
                return Status.SUSPENDED_EVSE  # the station is not offering any
            return Status.CHARGING
        if c.pilot is Pilot.A:
            return Status.AVAILABLE
        # plugged in with no transaction: before one starts, and for a moment after one ends
        return Status.FINISHING if c.transaction is not None else Status.PREPARING

    # ---- readings ---------------------------------------------------------------------------------

    def power_w(self, connector: int) -> float:
        return NOMINAL_V * self.connector(connector).drawn_a(self.clock)

    def boot(self) -> Event:
        """The station starts up and reports itself."""
        event = Event("boot", at=self.clock)
        self.last_heartbeat = self.clock
        self._report(event)
        return event

    def accept_boot(self, interval: int) -> None:
        self.booted = True
        self.boot_interval = max(1, interval)
        for c in self.connectors.values():
            self._report(Event("status", c.id, self.clock, status=c.status))
