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


def _real_coordinator(key_update_times):
    from custom_components.omnibattery.infra.coordinator import (
        MarstekVenusDataUpdateCoordinator,
    )

    coord = SimpleNamespace(_key_update_times=key_update_times)
    return lambda a, b: MarstekVenusDataUpdateCoordinator.readings_from_same_poll(
        coord, a, b
    )


def test_same_poll_matches_equal_key_timestamps():
    now = dt_util.utcnow()
    check = _real_coordinator({"ac_offgrid_power": now, "inverter_state": now})
    assert check("inverter_state", "ac_offgrid_power") is True


def test_same_poll_rejects_a_key_that_missed_a_cycle():
    now = dt_util.utcnow()
    check = _real_coordinator(
        {
            "ac_offgrid_power": now,
            "inverter_state": now - timedelta(seconds=5),
        }
    )
    assert check("inverter_state", "ac_offgrid_power") is False


def test_same_poll_rejects_a_key_that_was_never_read():
    check = _real_coordinator({"ac_offgrid_power": dt_util.utcnow()})
    assert check("inverter_state", "ac_offgrid_power") is False


async def test_partial_read_of_a_shared_group_does_not_trust_stale_bypass(
    monkeypatch,
):
    """ESPHome reads every key in one group and omits unavailable entities.

    The group is stamped as soon as one key is stored, so a per-group check
    would pair a fresh port load with the previous poll's Bypass.
    """
    import asyncio
    from unittest.mock import AsyncMock

    from homeassistant.util import dt as ha_dt

    from custom_components.omnibattery.drivers.base import ReadGroup
    from custom_components.omnibattery.infra.coordinator import (
        MarstekVenusDataUpdateCoordinator,
    )

    replies = iter(
        [
            {"inverter_state": INVERTER_BYPASS, "ac_offgrid_power": 116},
            {"ac_offgrid_power": 900},  # inverter_state entity unavailable
        ]
    )

    async def read_telemetry(keys):
        return next(replies)

    clock = iter([dt_util.utcnow(), dt_util.utcnow() + timedelta(seconds=2)])
    monkeypatch.setattr(ha_dt, "utcnow", lambda: next(clock))

    keys = ("inverter_state", "ac_offgrid_power")
    coord = SimpleNamespace(
        name="Venus",
        host="192.0.2.10",
        device_key="192.0.2.10_1",
        driver=SimpleNamespace(
            read_groups=[ReadGroup("high", keys)],
            read_telemetry=read_telemetry,
            control_dependency_keys=set(),
        ),
        _def_by_key={k: {"key": k} for k in keys},
        _get_entity_type=lambda definition, fallback_key=None: "sensor",
        _entity_registry=SimpleNamespace(
            async_get_entity_id=lambda *args: None, entities={}
        ),
        _is_shutting_down=False,
        _suspension_reset_time=None,
        _last_update_times={},
        _key_update_times={},
        _critical_group_failures={},
        boost_fast_poll_until=0.0,
        lock=asyncio.Lock(),
        _consecutive_failures=0,
        _max_failures_before_reconnect=99,
        _max_failures_before_suspend=100,
        _is_connected=True,
        data={},
        async_reconnect_fresh=AsyncMock(return_value=True),
        capabilities=SimpleNamespace(
            has_energy_counters=True, has_daily_energy_counters=True
        ),
        battery_capacity_kwh=0,
        _alarm_notifier=SimpleNamespace(check=AsyncMock()),
    )

    for _ in range(2):
        coord._last_update_times.clear()
        await MarstekVenusDataUpdateCoordinator._async_update_data(coord)

    assert coord.data["inverter_state"] == INVERTER_BYPASS  # stale, kept
    assert MarstekVenusDataUpdateCoordinator.readings_from_same_poll(
        coord, "inverter_state", "ac_offgrid_power"
    ) is False
