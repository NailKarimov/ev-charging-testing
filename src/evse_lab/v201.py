"""OCPP 2.0.1: the version new charge points are certified against.

The biggest change for a tester is the transaction. In 1.6 a session is three different messages and the
back office hands out the transaction number. In 2.0.1 there is one message, ``TransactionEvent``, with
``Started``, ``Updated`` and ``Ended``, the **station** chooses the transaction id, and every event of a
transaction carries ``seqNo`` so the back office can tell whether anything was lost. A station that was
offline can therefore hand over a whole finished session, in order, and nothing has to be guessed.
"""

from __future__ import annotations

import itertools
from typing import Any

from evse_lab.dialect import iso, sampled_value
from evse_lab.rpc import Call
from evse_lab.schemas import V201
from evse_lab.station import NOMINAL_V, ChargePoint, Event, Profile, Reason, Status

ACCEPTED = {"status": "Accepted"}
REJECTED = {"status": "Rejected"}

SUPPORTED = frozenset(
    {
        "RequestStartTransaction",
        "RequestStopTransaction",
        "Reset",
        "ChangeAvailability",
        "UnlockConnector",
        "SetChargingProfile",
        "ClearChargingProfile",
        "TriggerMessage",
        "GetVariables",
        "SetVariables",
        "DataTransfer",
    }
)

# Why a transaction ended, in the words of 2.0.1 (table "ReasonEnumType").
STOP_REASON = {
    Reason.LOCAL: "Local",
    Reason.REMOTE: "Remote",
    Reason.EV_DISCONNECTED: "EVDisconnected",
    Reason.POWER_LOSS: "PowerLoss",
    Reason.DE_AUTHORIZED: "DeAuthorized",
    Reason.OTHER: "Other",
}

VARIABLES: dict[tuple[str, str], str] = {
    ("OCPPCommCtrlr", "HeartbeatInterval"): "300",
    ("AlignedDataCtrlr", "Interval"): "900",
    ("SampledDataCtrlr", "TxUpdatedInterval"): "60",
    ("TxCtrlr", "EVConnectionTimeOut"): "60",
}
READ_ONLY = frozenset({("TxCtrlr", "EVConnectionTimeOut")})


class V201Dialect:
    version = V201
    name = "ocpp2.0.1"

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.variables = dict(VARIABLES)
        self._reported: dict[int, str] = {}  # the last status actually sent, per EVSE

    # ---- the station speaks ---------------------------------------------------------------------

    def calls_for(self, station: ChargePoint, event: Event) -> list[Call]:
        kind = event.kind
        if kind == "boot":
            return [
                self._call(
                    "BootNotification",
                    {
                        "reason": "PowerUp",
                        "chargingStation": {
                            "model": station.model,
                            "vendorName": station.vendor,
                            "serialNumber": station.serial,
                            "firmwareVersion": station.firmware,
                        },
                    },
                )
            ]
        if kind == "status":
            assert event.status is not None
            # 2.0.1 has a shorter list of statuses than the station has internal states: Preparing,
            # Charging, SuspendedEV, SuspendedEVSE and Finishing all read "Occupied". Sending the word
            # again when it has not changed is noise the standard does not ask for, so it is not sent.
            name = _status_name(event.status)
            if not event.forced and self._reported.get(event.connector) == name:
                return []
            self._reported[event.connector] = name
            return [
                self._call(
                    "StatusNotification",
                    {
                        "timestamp": iso(event.at),
                        "connectorStatus": name,
                        "evseId": event.connector,
                        "connectorId": 1,
                    },
                )
            ]
        if kind == "authorize":
            return [self._call("Authorize", {"idToken": _id_token(event.id_tag)})]
        if kind in ("started", "sample", "stopped"):
            return [self._transaction_event(station, event)]
        if kind == "heartbeat":
            return [self._call("Heartbeat", {})]
        raise AssertionError(f"no OCPP 2.0.1 message for event {kind!r}")

    def _transaction_event(self, station: ChargePoint, event: Event) -> Call:
        transaction = event.transaction
        assert transaction is not None
        connector = station.connector(event.connector)
        amps = connector.drawn_a(event.at)
        info: dict[str, Any] = {"transactionId": _transaction_id(transaction.local_id)}
        if event.kind == "started":
            kind, trigger = "Started", "Authorized"
            meter_wh = transaction.meter_start
            info["chargingState"] = "EVConnected"
        elif event.kind == "sample":
            kind, trigger = "Updated", "MeterValuePeriodic"
            meter_wh = event.meter_wh
            info["chargingState"] = "Charging" if amps > 0 else "SuspendedEVSE"
        else:
            kind, trigger = "Ended", _end_trigger(event.reason)
            assert transaction.meter_stop is not None
            meter_wh = transaction.meter_stop
            info["chargingState"] = "Idle"
            info["stoppedReason"] = STOP_REASON[event.reason or Reason.LOCAL]
        payload: dict[str, Any] = {
            "eventType": kind,
            "timestamp": iso(event.at),
            "triggerReason": trigger,
            "seqNo": transaction.seq_no,
            "transactionInfo": info,
            "evse": {"id": event.connector, "connectorId": 1},
            "meterValue": [
                {"timestamp": iso(event.at), "sampledValue": sampled_value(V201, meter_wh, NOMINAL_V * amps, amps)}
            ],
        }
        if event.kind == "started":
            payload["idToken"] = _id_token(transaction.id_tag)
        if event.offline:
            payload["offline"] = True  # it happened while nobody could be told
        transaction.seq_no += 1
        return self._call("TransactionEvent", payload)

    def on_result(self, station: ChargePoint, call: Call, payload: dict[str, Any]) -> None:
        if call.action == "BootNotification" and payload.get("status") == "Accepted":
            station.accept_boot(int(payload.get("interval", 300)))

    # ---- the back office orders ----------------------------------------------------------------

    def supported(self) -> frozenset[str]:
        return SUPPORTED

    def handle(self, station: ChargePoint, call: Call) -> dict[str, Any]:
        action, payload = call.action, call.payload
        if action == "RequestStartTransaction":
            return self._remote_start(station, payload)
        if action == "RequestStopTransaction":
            return self._remote_stop(station, payload)
        if action == "Reset":
            kind = str(payload.get("type", "Immediate"))
            station.request_reset("Hard" if kind == "Immediate" else "Soft")
            return ACCEPTED
        if action == "ChangeAvailability":
            operative = payload.get("operationalStatus") == "Operative"
            evse = payload.get("evse") or {}
            number = int(evse.get("id", 0))
            busy = any(
                c.transaction is not None and c.transaction.running
                for n, c in station.connectors.items()
                if number in (0, n)
            )
            station.set_operative(number, operative)
            return {"status": "Scheduled" if busy and not operative else "Accepted"}
        if action == "UnlockConnector":
            result = station.unlock(int(payload["evseId"]))
            return {"status": {"Unlocked": "Unlocked", "UnlockFailed": "UnlockFailed"}.get(result, "UnknownConnector")}
        if action == "SetChargingProfile":
            return self._set_profile(station, payload)
        if action == "ClearChargingProfile":
            criteria = payload.get("chargingProfileCriteria") or {}
            removed = station.clear_profiles(
                connector=criteria.get("evseId"), profile_id=payload.get("chargingProfileId")
            )
            return {"status": "Accepted" if removed else "Unknown"}
        if action == "TriggerMessage":
            return self._trigger(station, payload)
        if action == "GetVariables":
            return self._get_variables(payload)
        if action == "SetVariables":
            return self._set_variables(station, payload)
        if action == "DataTransfer":
            return {"status": "UnknownVendorId"}
        raise AssertionError(f"{action} is in SUPPORTED but is not handled")

    # ---- the orders in detail ---------------------------------------------------------------------

    def _remote_start(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        id_tag = str(payload["idToken"]["idToken"])
        number = payload.get("evseId")
        if number is None:
            free = [n for n, c in station.connectors.items() if c.transaction is None or not c.transaction.running]
            if not free:
                return REJECTED
            number = free[0]
        connector = station.connectors.get(int(number))
        if connector is None or not connector.operative:
            return REJECTED
        if connector.transaction is not None and connector.transaction.running:
            return REJECTED
        if "chargingProfile" in payload:
            try:
                self._apply_profile(station, int(number), payload["chargingProfile"])
            except (KeyError, IndexError, TypeError, ValueError):
                return REJECTED
        station.start_transaction(id_tag, int(number))
        return ACCEPTED

    def _remote_stop(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        wanted = str(payload["transactionId"])
        for number, connector in station.connectors.items():
            transaction = connector.transaction
            if transaction is not None and transaction.running and _transaction_id(transaction.local_id) == wanted:
                station.stop_transaction(number, Reason.REMOTE)
                return ACCEPTED
        return REJECTED

    def _set_profile(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        number = int(payload["evseId"])
        if number not in station.connectors:
            return REJECTED
        try:
            self._apply_profile(station, number, payload["chargingProfile"])
        except (KeyError, IndexError, TypeError, ValueError):
            return REJECTED
        return ACCEPTED

    def _apply_profile(self, station: ChargePoint, connector: int, raw: dict[str, Any]) -> None:
        schedule = raw["chargingSchedule"][0]
        if schedule.get("chargingRateUnit") != "A":
            raise ValueError("this station limits current, not power")
        period = schedule["chargingSchedulePeriod"][0]
        start = schedule.get("startSchedule")
        duration = schedule.get("duration")
        valid_from = _clock(start) if start else None
        station.set_profile(
            connector,
            Profile(
                id=int(raw["id"]),
                limit_a=float(period["limit"]),
                stack_level=int(raw.get("stackLevel", 0)),
                valid_from=valid_from,
                valid_to=(valid_from + int(duration)) if valid_from is not None and duration else None,
                purpose=str(raw.get("chargingProfilePurpose", "TxDefaultProfile")),
            ),
        )

    def _trigger(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        wanted = payload["requestedMessage"]
        evse = payload.get("evse") or {}
        number = evse.get("id")
        if wanted == "BootNotification":
            station.boot()
            return ACCEPTED
        if wanted == "Heartbeat":
            station.events.append(Event("heartbeat", at=station.clock))
            return ACCEPTED
        if wanted == "StatusNotification":
            for n, c in station.connectors.items():
                if number in (None, n):
                    station.events.append(Event("status", n, station.clock, status=c.status, forced=True))
            return ACCEPTED
        if wanted == "TransactionEvent":
            for n, c in station.connectors.items():
                if number in (None, n) and c.transaction is not None and c.transaction.running:
                    station.events.append(
                        Event("sample", n, station.clock, transaction=c.transaction, meter_wh=int(station.meter_wh))
                    )
            return ACCEPTED
        return {"status": "NotImplemented"}

    def _get_variables(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = []
        for wanted in payload["getVariableData"]:
            component = str(wanted["component"]["name"])
            variable = str(wanted["variable"]["name"])
            value = self.variables.get((component, variable))
            row: dict[str, Any] = {
                "attributeStatus": "Accepted" if value is not None else "UnknownVariable",
                "component": {"name": component},
                "variable": {"name": variable},
            }
            if value is not None:
                row["attributeValue"] = value
            result.append(row)
        return {"getVariableResult": result}

    def _set_variables(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        result = []
        for wanted in payload["setVariableData"]:
            component = str(wanted["component"]["name"])
            variable = str(wanted["variable"]["name"])
            value = str(wanted["attributeValue"])
            key = (component, variable)
            if key not in self.variables:
                status = "UnknownVariable"
            elif key in READ_ONLY or variable.endswith("Interval") and not value.isdigit():
                status = "Rejected"
            else:
                self.variables[key] = value
                status = "Accepted"
                if key == ("OCPPCommCtrlr", "HeartbeatInterval"):
                    station.boot_interval = max(1, int(value))
            result.append({"attributeStatus": status, "component": {"name": component}, "variable": {"name": variable}})
        return {"setVariableResult": result}

    def _call(self, action: str, payload: dict[str, Any]) -> Call:
        return Call(f"{next(self._ids):08d}", action, payload)


def _status_name(status: Status) -> str:
    """2.0.1 has a shorter list of connector states than 1.6."""
    return {
        Status.PREPARING: "Occupied",
        Status.CHARGING: "Occupied",
        Status.SUSPENDED_EV: "Occupied",
        Status.SUSPENDED_EVSE: "Occupied",
        Status.FINISHING: "Occupied",
    }.get(status, status.value)


def _id_token(id_tag: str) -> dict[str, Any]:
    return {"idToken": id_tag, "type": "ISO14443"}


def _transaction_id(local_id: int) -> str:
    """In 2.0.1 the station chooses the id, and it is text, not a number."""
    return f"TX-{local_id:06d}"


def _end_trigger(reason: Reason | None) -> str:
    if reason is Reason.EV_DISCONNECTED:
        return "EVCommunicationLost"
    if reason is Reason.REMOTE:
        return "RemoteStop"
    return "StopAuthorized"


def _clock(timestamp: str) -> int:
    from evse_lab.dialect import clock_of

    return clock_of(timestamp)
