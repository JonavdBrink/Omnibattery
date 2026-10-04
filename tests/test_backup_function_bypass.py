"""Tests for ``_is_backup_function_active`` while the inverter reports Bypass.

With the grid present a Venus passes it through to the backup port, so a
constant load connected there reads as off-grid power. That must not count as
an outage: only the backup state means the battery feeds the port.

The method is exercised unbound with light stubs for ``self`` and the
coordinator (same pattern as test_charge_hysteresis.py).
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.omnibattery import ChargeDischargeController

INVERTER_BACKUP = 4
INVERTER_BYPASS = 6


class _Coord:
    """Identity-hashable coordinator stand-in (used as a dict key)."""

    name = "Venus"
    backup_offgrid_threshold = 50

    def __init__(self, data, *, same_poll=True):
        self.data = data
        self._same_poll = same_poll

    def readings_from_same_poll(self, key_a, key_b):
        return self._same_poll


def _active(coord, controller=None):
    controller = controller or SimpleNamespace(_backup_cooldown_until={})
    return ChargeDischargeController._is_backup_function_active(controller, coord)


def _data(*, offgrid, inverter_state, backup_function=0):
    return {
        "backup_function": backup_function,
        "ac_offgrid_power": offgrid,
        "inverter_state": inverter_state,
    }


def test_bypass_with_constant_load_is_not_backup():
    controller = SimpleNamespace(_backup_cooldown_until={})
    coord = _Coord(_data(offgrid=116, inverter_state=INVERTER_BYPASS))
    assert _active(coord, controller) is False
    assert coord not in controller._backup_cooldown_until


def test_stale_bypass_does_not_hide_fresh_port_load():
    """Grid dropped: the port load is fresh, the inverter-state read failed."""
    controller = SimpleNamespace(_backup_cooldown_until={})
    coord = _Coord(
        _data(offgrid=116, inverter_state=INVERTER_BYPASS), same_poll=False
    )
    assert _active(coord, controller) is True
    assert coord in controller._backup_cooldown_until


def test_backup_state_with_load_is_backup():
    controller = SimpleNamespace(_backup_cooldown_until={})
    coord = _Coord(_data(offgrid=116, inverter_state=INVERTER_BACKUP))
    assert _active(coord, controller) is True
    assert coord in controller._backup_cooldown_until


def test_load_above_threshold_without_inverter_state_is_backup():
    coord = _Coord(_data(offgrid=116, inverter_state=None))
    assert _active(coord) is True


def test_label_state_does_not_count_as_bypass():
    coord = _Coord(_data(offgrid=116, inverter_state="Bypass"))
    assert _active(coord) is True


def test_cooldown_after_outage_still_runs_in_bypass():
    coord = _Coord(_data(offgrid=116, inverter_state=INVERTER_BYPASS))
    controller = SimpleNamespace(
        _backup_cooldown_until={coord: dt_util.utcnow() + timedelta(minutes=3)}
    )
    assert _active(coord, controller) is True


def test_expired_cooldown_releases_in_bypass():
    coord = _Coord(_data(offgrid=116, inverter_state=INVERTER_BYPASS))
    controller = SimpleNamespace(
        _backup_cooldown_until={coord: dt_util.utcnow() - timedelta(minutes=1)}
    )
    assert _active(coord, controller) is False
    assert coord not in controller._backup_cooldown_until


@pytest.mark.parametrize("inverter_state", [INVERTER_BYPASS, INVERTER_BACKUP])
def test_switch_off_is_never_backup(inverter_state):
    coord = _Coord(_data(offgrid=500, inverter_state=inverter_state, backup_function=1))
    assert _active(coord) is False


def _real_coordinator(last_update_times):
    from custom_components.omnibattery.infra.coordinator import (
        MarstekVenusDataUpdateCoordinator,
    )

    coord = SimpleNamespace(_last_update_times=last_update_times)
    return lambda a, b: MarstekVenusDataUpdateCoordinator.readings_from_same_poll(
        coord, a, b
    )


def test_same_poll_matches_equal_group_timestamps():
    now = dt_util.utcnow()
    check = _real_coordinator({("ac_offgrid_power",): now, ("inverter_state",): now})
    assert check("inverter_state", "ac_offgrid_power") is True


def test_same_poll_rejects_a_group_that_missed_a_cycle():
    now = dt_util.utcnow()
    check = _real_coordinator(
        {
            ("ac_offgrid_power",): now,
            ("inverter_state",): now - timedelta(seconds=5),
        }
    )
    assert check("inverter_state", "ac_offgrid_power") is False


def test_same_poll_matches_keys_in_one_group():
    now = dt_util.utcnow()
    check = _real_coordinator({("inverter_state", "ac_offgrid_power"): now})
    assert check("inverter_state", "ac_offgrid_power") is True


def test_same_poll_rejects_a_key_that_was_never_read():
    check = _real_coordinator({("ac_offgrid_power",): dt_util.utcnow()})
    assert check("inverter_state", "ac_offgrid_power") is False
