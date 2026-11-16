"""Smart charging: the grid operator tells the station how much current it may give.

This is what makes a car park full of chargers possible on one supply. A profile is a current limit with
a stack level and a validity window; the station obeys the strictest one in force and goes back to full
power when it expires. Below 6 A no car may charge at all, so a very low limit means a pause, not a trickle.
"""

from __future__ import annotations

import pytest

from evse_lab.node import Exchange
from evse_lab.station import MIN_CHARGE_A, NOMINAL_V, Status
from support.evidence import step
from support.rig import Rig


def amps(rig: Rig, connector: int = 1) -> float:
    return rig.station.connector(connector).drawn_a(rig.station.clock)


@pytest.mark.smoke
def test_a_limit_from_the_operator_is_obeyed(booted: Rig) -> None:
    """Told to allow only 10 A, the station lets the car take 10 A instead of the 16 A it asked for."""
    booted.start_session()
    booted.charge(60)
    assert amps(booted) == 16.0
    with step("The operator sends a limit of 10 A"):
        answer = booted.profile(10)
    assert answer.payload["status"] == "Accepted"
    booted.charge(10)
    assert amps(booted) == 10.0
    assert booted.station.power_w(1) == pytest.approx(NOMINAL_V * 10)


def test_a_limit_above_what_the_car_asks_for_changes_nothing(booted: Rig) -> None:
    """A limit of 20 A leaves a car that only asks for 16 A charging at 16 A."""
    booted.start_session()
    booted.profile(20)
    booted.charge(10)
    assert amps(booted) == 16.0
    assert booted.station.connector(1).status is Status.CHARGING


@pytest.mark.parametrize("limit", [6, 8, 13, 16, 32])
def test_the_station_gives_exactly_the_current_it_is_allowed(booted: Rig, limit: int) -> None:
    """Whatever limit is set, the car gets that current, or its own maximum if that is lower."""
    booted.station.connector(1).car_max_a = 32.0
    booted.start_session()
    booted.profile(limit)
    booted.charge(10)
    assert amps(booted) == float(limit)


def test_a_limit_below_six_amps_pauses_the_charging_instead_of_trickling(booted: Rig) -> None:
    """A limit under 6 A is not a slow charge: the pilot cannot ask for it, so charging pauses."""
    booted.start_session()
    booted.charge(60)
    with step("The operator sends a limit of 4 A, below what the pilot can ask for"):
        booted.profile(4)
        booted.charge(5)
    assert MIN_CHARGE_A == 6.0
    assert amps(booted) == 0.0
    assert booted.station.connector(1).status is Status.SUSPENDED_EVSE, "the station is the one holding back"
    frozen = int(booted.station.meter_wh)
    booted.charge(120)
    assert int(booted.station.meter_wh) == frozen
    assert booted.csms.session_of("CARD-OK").running, "the session is paused, not ended"


def test_removing_the_limit_brings_the_power_back(booted: Rig) -> None:
    """When the profile is cleared the car goes back to the current it asked for."""
    booted.start_session()
    booted.profile(8)
    booted.charge(60)
    assert amps(booted) == 8.0
    with step("The operator clears the profile"):
        answer = _clear(booted)
    assert answer.payload["status"] == "Accepted"
    booted.charge(5)
    assert amps(booted) == 16.0


def test_clearing_a_profile_that_is_not_there_says_so(booted: Rig) -> None:
    """Clearing a profile the station does not have is answered with Unknown, not with Accepted."""
    booted.start_session()
    assert _clear(booted).payload["status"] == "Unknown"


def test_the_higher_stack_level_wins(booted: Rig) -> None:
    """When two profiles are in force the one on the higher stack level decides, even if it allows more."""
    booted.start_session()
    with step("A background profile allows 10 A"):
        booted.profile(10, profile_id=1, stack_level=0)
        booted.charge(5)
    assert amps(booted) == 10.0
    with step("A profile on a higher level allows 14 A"):
        booted.profile(14, profile_id=2, stack_level=1)
        booted.charge(5)
    assert amps(booted) == 14.0, "the higher level replaces the lower one, it does not add to it"


def test_two_profiles_on_the_same_level_leave_the_stricter_one(booted: Rig) -> None:
    """With two profiles on one level the station keeps to the smaller current: it may never give more."""
    booted.start_session()
    booted.profile(14, profile_id=1, stack_level=0)
    booted.profile(9, profile_id=2, stack_level=0)
    booted.charge(5)
    assert amps(booted) == 9.0


def test_a_profile_that_starts_later_does_not_bite_yet(booted: Rig) -> None:
    """A profile with a start time in the future leaves the car alone until that time comes."""
    from evse_lab.dialect import iso

    booted.start_session()
    start = booted.station.clock + 600
    with step("A profile that only starts in ten minutes"):
        booted.profile(6, start=iso(start), duration=600)
        booted.charge(300, step=60)
    assert amps(booted) == 16.0
    with step("Ten minutes later"):
        booted.charge(400, step=60)
    assert amps(booted) == 6.0


def test_a_profile_stops_biting_when_its_window_ends(booted: Rig) -> None:
    """When a profile's window runs out the station goes back to full power by itself."""
    from evse_lab.dialect import iso

    booted.start_session()
    with step("A profile of 8 A for five minutes"):
        booted.profile(8, start=iso(booted.station.clock), duration=300)
        booted.charge(60, step=30)
    assert amps(booted) == 8.0
    with step("Five minutes later the window is over"):
        booted.charge(300, step=60)
    assert amps(booted) == 16.0


def test_a_profile_only_touches_the_connector_it_names(booted: Rig) -> None:
    """A limit set on one connector leaves the car on the other connector charging as before."""
    booted.start_session("CARD-OK", connector=1)
    booted.start_session("CARD-OK-2", connector=2)
    booted.charge(60)
    booted.profile(7, connector=1)
    booted.charge(5)
    assert amps(booted, 1) == 7.0
    assert amps(booted, 2) == 16.0


def test_a_profile_in_watts_is_refused_by_a_station_that_limits_current(booted: Rig) -> None:
    """This station limits current, so a profile written in watts is refused rather than guessed at."""
    booted.start_session()
    answer = booted.profile(3000, unit="W")
    assert answer.payload["status"] == "Rejected"
    booted.charge(5)
    assert amps(booted) == 16.0


def test_a_profile_for_a_connector_that_does_not_exist_is_refused(booted: Rig) -> None:
    """A profile for connector 9 is refused: this station has two."""
    booted.start_session()
    answer = booted.profile(10, connector=9)
    assert answer.payload["status"] == "Rejected"


def test_the_limit_survives_a_pause_in_charging(booted: Rig) -> None:
    """A limit set before the car paused is still in force when the car starts taking power again."""
    booted.start_session()
    booted.profile(9)
    booted.charge(60)
    booted.car_ready(False)
    booted.charge(120)
    booted.car_ready(True)
    booted.charge(10)
    assert amps(booted) == 9.0


def _clear(rig: Rig) -> Exchange:
    if rig.version == "1.6":
        return rig.order("ClearChargingProfile", {"id": 1})
    return rig.order("ClearChargingProfile", {"chargingProfileId": 1})
