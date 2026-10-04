"""An unpriced night after the last published price stays protected (#530)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.omnibattery.pricing.curtailment import BatterySnapshot
from custom_components.omnibattery.pricing.high_price_discharge import (
    REASON_COVERAGE_GAP,
    HorizonSlot,
    plan_high_price_discharge,
)


DAY = datetime(2026, 6, 1, tzinfo=ZoneInfo("UTC"))


def _slot(hour, *, price=None, buy=None, consumption=0.0):
    start = DAY + timedelta(hours=hour)
    return HorizonSlot(start, start + timedelta(hours=1), price, buy, consumption, 0.0)


def _plan(slots, *, usable, **kwargs):
    battery = BatterySnapshot("battery", 20 + usable * 10, 10.0, 100.0, 20.0, 5000.0, True, True)
    return plan_high_price_discharge(
        slots, [battery], now=DAY, horizon_end=slots[-1].end, enabled=True,
        additional_cost_per_kwh=0.05, **kwargs,
    )


# Morning peak, cheap priced demand later today, then the unpriced night.
SLOTS = [_slot(0, price=.5, buy=.1), _slot(1, buy=.1, consumption=1),
         _slot(2, consumption=2)]


def _sold(plan):
    return sum(allocation.energy_kwh for allocation in plan.allocations)


def _linked_starts(plan):
    return {link.demand_start for a in plan.allocations for link in a.demand_links}


def test_tail_counts_as_protected_demand_and_is_no_longer_a_blocker():
    plan = _plan(SLOTS, usable=4)
    assert plan.reason != REASON_COVERAGE_GAP
    assert plan.protected_demand_kwh == pytest.approx(3)
    assert _sold(plan) == pytest.approx(1)
    assert _linked_starts(plan) == {SLOTS[1].start}


def test_chronological_battery_never_sells_into_a_covered_tail():
    # Run-out would land in the unpriced night: nothing is sold.
    assert _plan(SLOTS, usable=3).allocations == ()
    assert _plan(SLOTS, usable=2.5).allocations == ()


def test_tail_already_uncovered_still_sells_against_priced_demand():
    slots = [_slot(0, price=.5, buy=.1), _slot(1, buy=.1, consumption=2), _slot(2, consumption=2)]
    plan = _plan(slots, usable=1.5)
    assert _sold(plan) == pytest.approx(1.5)
    assert _linked_starts(plan) == {slots[1].start}


def test_reserve_steered_battery_keeps_the_tail():
    plan = _plan(SLOTS, usable=2.5, chronological_coverage=False)
    assert _sold(plan) == pytest.approx(.5)
    assert _linked_starts(plan) == {SLOTS[1].start}


def test_unpriced_gap_before_priced_demand_still_blocks():
    slots = [_slot(0, price=.5, buy=.1), _slot(1, consumption=1), _slot(2, buy=.1, consumption=2)]
    assert _plan(slots, usable=5).allocations == ()
