"""OCPP 1.6J: the version running on most charge points in the field today.

A session is three separate messages: ``StartTransaction`` (the back office answers with the number of
the transaction), ``MeterValues`` while it runs, and ``StopTransaction``. The back office owns the
transaction number, which is why a station that starts a transaction while offline has to wait for it.
"""

from __future__ import annotations

import itertools
from typing import Any

from evse_lab.dialect import iso, sampled_value
from evse_lab.rpc import Call
from evse_lab.schemas import V16
from evse_lab.station import NOMINAL_V, ChargePoint, Event, Profile, Reason, Status

ACCEPTED = {"status": "Accepted"}
REJECTED = {"status": "Rejected"}

# Orders a charge point of this project accepts from the back office.
SUPPORTED = frozenset(
    {
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "Reset",
        "ChangeAvailability",
        "UnlockConnector",
        "SetChargingProfile",
        "ClearChargingProfile",
        "TriggerMessage",
        "GetConfiguration",
        "ChangeConfiguration",
        "DataTransfer",
    }
)

CONFIGURATION: dict[str, str] = {
    "HeartbeatInterval": "300",
    "MeterValueSampleInterval": "60",
    "NumberOfConnectors": "2",
    "ConnectionTimeOut": "60",
    "AuthorizeRemoteTxRequests": "false",
}
READ_ONLY = frozenset({"NumberOfConnectors"})


class V16Dialect:
    version = V16
    name = "ocpp1.6"

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.configuration = dict(CONFIGURATION)

    # ---- the station speaks ---------------------------------------------------------------------

    def calls_for(self, station: ChargePoint, event: Event) -> list[Call]:
        kind = event.kind
        if kind == "boot":
            return [
                self._call(
                    "BootNotification",
                    {
                        "chargePointVendor": station.vendor,
                        "chargePointModel": station.model,
                        "chargePointSerialNumber": station.serial,
                        "firmwareVersion": station.firmware,
                    },
                )
            ]
        if kind == "status":
            assert event.status is not None
            return [
                self._call(
                    "StatusNotification",
                    {
                        "connectorId": event.connector,
                        "errorCode": "NoError" if event.status is not Status.FAULTED else "OtherError",
                        "status": event.status.value,
                        "timestamp": iso(event.at),
                    },
                )
            ]
        if kind == "authorize":
            return [self._call("Authorize", {"idTag": event.id_tag})]
        if kind == "started":
            transaction = event.transaction
            assert transaction is not None
            return [
                self._call(
                    "StartTransaction",
                    {
                        "connectorId": transaction.connector,
                        "idTag": transaction.id_tag,
                        "meterStart": transaction.meter_start,
                        "timestamp": iso(transaction.started_at),
                    },
                )
            ]
        if kind == "sample":
            transaction = event.transaction
            assert transaction is not None
            connector = station.connector(event.connector)
            amps = connector.drawn_a(event.at)
            payload: dict[str, Any] = {
                "connectorId": event.connector,
                "meterValue": [
                    {
                        "timestamp": iso(event.at),
                        "sampledValue": sampled_value(V16, event.meter_wh, NOMINAL_V * amps, amps),
                    }
                ],
            }
            if transaction.remote_id is not None:
                payload["transactionId"] = transaction.remote_id
            return [self._call("MeterValues", payload)]
        if kind == "stopped":
            transaction = event.transaction
            assert transaction is not None
            assert transaction.meter_stop is not None
            stop: dict[str, Any] = {
                "meterStop": transaction.meter_stop,
                "timestamp": iso(event.at),
                "transactionId": transaction.remote_id if transaction.remote_id is not None else -transaction.local_id,
                "reason": (event.reason or Reason.LOCAL).value,
                "idTag": transaction.id_tag,
            }
            return [self._call("StopTransaction", stop)]
        if kind == "heartbeat":
            return [self._call("Heartbeat", {})]
        raise AssertionError(f"no OCPP 1.6 message for event {kind!r}")

    def on_result(self, station: ChargePoint, call: Call, payload: dict[str, Any]) -> None:
        if call.action == "BootNotification":
            if payload.get("status") == "Accepted":
                station.accept_boot(int(payload.get("interval", 300)))
        elif call.action == "StartTransaction":
            number = payload.get("transactionId")
            for connector in station.connectors.values():
                transaction = connector.transaction
                if transaction is not None and transaction.remote_id is None and transaction.id_tag == _tag(call):
                    transaction.remote_id = int(number) if number is not None else None
                    break

    # ---- the back office orders ----------------------------------------------------------------

    def supported(self) -> frozenset[str]:
        return SUPPORTED

    def handle(self, station: ChargePoint, call: Call) -> dict[str, Any]:
        action, payload = call.action, call.payload
        if action == "RemoteStartTransaction":
            return self._remote_start(station, payload)
        if action == "RemoteStopTransaction":
            return self._remote_stop(station, payload)
        if action == "Reset":
            station.request_reset(str(payload.get("type", "Soft")))
            return ACCEPTED
        if action == "ChangeAvailability":
            connector = int(payload["connectorId"])
            operative = payload.get("type") == "Operative"
            busy = any(
                c.transaction is not None and c.transaction.running
                for n, c in station.connectors.items()
                if connector in (0, n)
            )
            station.set_operative(connector, operative)
            return {"status": "Scheduled" if busy and not operative else "Accepted"}
        if action == "UnlockConnector":
            return {"status": station.unlock(int(payload["connectorId"]))}
        if action == "SetChargingProfile":
            return self._set_profile(station, payload)
        if action == "ClearChargingProfile":
            removed = station.clear_profiles(
                connector=payload.get("connectorId"),
                profile_id=payload.get("id"),
            )
            return {"status": "Accepted" if removed else "Unknown"}
        if action == "TriggerMessage":
            return self._trigger(station, payload)
        if action == "GetConfiguration":
            return self._get_configuration(payload)
        if action == "ChangeConfiguration":
            return self._change_configuration(station, payload)
        if action == "DataTransfer":
            return {"status": "UnknownVendorId"}
        raise AssertionError(f"{action} is in SUPPORTED but is not handled")

    # ---- the orders in detail ---------------------------------------------------------------------

    def _remote_start(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        id_tag = str(payload["idTag"])
        number = payload.get("connectorId")
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
            self._apply_profile(station, int(number), payload["chargingProfile"])
        station.start_transaction(id_tag, int(number))
        return ACCEPTED

    def _remote_stop(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        wanted = int(payload["transactionId"])
        for number, connector in station.connectors.items():
            transaction = connector.transaction
            if transaction is not None and transaction.running and transaction.remote_id == wanted:
                station.stop_transaction(number, Reason.REMOTE)
                return ACCEPTED
        return REJECTED

    def _set_profile(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        connector = int(payload["connectorId"])
        if connector not in station.connectors:
            return REJECTED
        try:
            self._apply_profile(station, connector, payload["csChargingProfiles"])
        except (KeyError, IndexError, TypeError, ValueError):
            return REJECTED
        return ACCEPTED

    def _apply_profile(self, station: ChargePoint, connector: int, raw: dict[str, Any]) -> None:
        schedule = raw["chargingSchedule"]
        if schedule.get("chargingRateUnit") != "A":
            raise ValueError("this station limits current, not power")
        period = schedule["chargingSchedulePeriod"][0]
        start = schedule.get("startSchedule")
        duration = schedule.get("duration")
        valid_from = _clock(start) if start else None
        station.set_profile(
            connector,
            Profile(
                id=int(raw["chargingProfileId"]),
                limit_a=float(period["limit"]),
                stack_level=int(raw.get("stackLevel", 0)),
                valid_from=valid_from,
                valid_to=(valid_from + int(duration)) if valid_from is not None and duration else None,
                purpose=str(raw.get("chargingProfilePurpose", "TxDefaultProfile")),
            ),
        )

    def _trigger(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        wanted = payload["requestedMessage"]
        number = payload.get("connectorId")
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
        if wanted == "MeterValues":
            for n, c in station.connectors.items():
                if number in (None, n) and c.transaction is not None and c.transaction.running:
                    station.events.append(
                        Event("sample", n, station.clock, transaction=c.transaction, meter_wh=int(station.meter_wh))
                    )
            return ACCEPTED
        return {"status": "NotImplemented"}

    def _get_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        wanted = payload.get("key")
        if not wanted:
            keys = sorted(self.configuration)
            unknown: list[str] = []
        else:
            keys = [k for k in wanted if k in self.configuration]
            unknown = [k for k in wanted if k not in self.configuration]
        result: dict[str, Any] = {
            "configurationKey": [
                {"key": key, "readonly": key in READ_ONLY, "value": self.configuration[key]} for key in keys
            ]
        }
        if unknown:
            result["unknownKey"] = unknown
        return result

    def _change_configuration(self, station: ChargePoint, payload: dict[str, Any]) -> dict[str, Any]:
        key, value = str(payload["key"]), str(payload["value"])
        if key not in self.configuration:
            return {"status": "NotSupported"}
        if key in READ_ONLY:
            return {"status": "Rejected"}
        if not value.isdigit() and key.endswith("Interval"):
            return {"status": "Rejected"}
        self.configuration[key] = value
        if key == "HeartbeatInterval":
            station.boot_interval = max(1, int(value))
        return ACCEPTED

    # ---- helpers ---------------------------------------------------------------------------------

    def _call(self, action: str, payload: dict[str, Any]) -> Call:
        return Call(f"{next(self._ids):08d}", action, payload)


def _tag(call: Call) -> str:
    return str(call.payload.get("idTag", ""))


def _clock(timestamp: str) -> int:
    from evse_lab.dialect import clock_of

    return clock_of(timestamp)
