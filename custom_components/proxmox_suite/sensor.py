from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BACKEND_PVE, BACKEND_PBS
from .coordinator import ProxmoxCoordinator

# IEC GiB (base 1024)
_GIB = 1024 * 1024 * 1024


def _bytes_to_gib(v: Any, precision: int = 1) -> float | None:
    if v is None:
        return None
    try:
        b = float(v)
    except Exception:
        return None
    return round(b / _GIB, precision)


def _cpu_to_percent(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    # Proxmox usually returns 0..1 for CPU usage
    if f <= 1.0:
        return round(f * 100.0, 1)
    return round(f, 1)


def _percent(used: Any, total: Any) -> float | None:
    try:
        u = float(used)
        t = float(total)
        if t <= 0:
            return None
        return round((u / t) * 100.0, 2)
    except Exception:
        return None


def _mem_percent_from_status(status: dict[str, Any] | None) -> float | None:
    """Compute memory usage % from Proxmox status dict (uses raw bytes)."""
    if not isinstance(status, dict):
        return None
    mem = status.get("memory") or {}
    if not isinstance(mem, dict):
        return None
    used = mem.get("used")
    total = mem.get("total")
    return _percent(used, total)


def _load_1m(loadavg: Any) -> float | None:
    try:
        if isinstance(loadavg, list) and loadavg:
            return round(float(loadavg[0]), 2)
        if isinstance(loadavg, dict):
            v = loadavg.get("0") if "0" in loadavg else loadavg.get(0)
            return round(float(v), 2) if v is not None else None
    except Exception:
        return None
    return None


def _tasks_list(tasks: Any) -> list[dict[str, Any]]:
    """Normalize tasks to a list of dicts; return empty list if unavailable."""
    if not isinstance(tasks, list):
        return []
    out: list[dict[str, Any]] = []
    for t in tasks:
        if isinstance(t, dict):
            out.append(t)
    return out


def _task_is_running(t: dict[str, Any]) -> bool:
    st = (t.get("status") or t.get("state") or "").lower()
    if st in ("running", "active"):
        return True

    endtime = t.get("endtime")
    if endtime is None or endtime == "":
        return True
    try:
        float(endtime)
        return False
    except Exception:
        return True


def _task_is_success(t: dict[str, Any]) -> bool:
    st = (t.get("status") or t.get("state") or "").lower()
    return st in ("ok", "success")


def _task_endtime(t: dict[str, Any]) -> float | None:
    endtime = t.get("endtime")
    if endtime is None:
        return None
    try:
        return float(endtime)
    except Exception:
        return None


def _count_failed_tasks_last_24h(tasks: Any) -> int:
    """Count tasks finished within last 24h with a non-success status."""
    lst = _tasks_list(tasks)
    if not lst:
        return 0

    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - 24 * 3600

    failed = 0
    for t in lst:
        end = _task_endtime(t)
        if end is None or end < cutoff:
            continue

        if _task_is_running(t):
            continue

        if _task_is_success(t):
            continue

        exitstatus = (t.get("exitstatus") or "").lower()
        if exitstatus in ("ok", "success"):
            continue

        failed += 1

    return failed


def _count_running_tasks(tasks: Any) -> int:
    """Count tasks currently running."""
    lst = _tasks_list(tasks)
    if not lst:
        return 0
    return sum(1 for t in lst if _task_is_running(t))


def _tasks_debug_attrs(tasks: Any) -> dict[str, Any]:
    lst = _tasks_list(tasks)
    statuses = []
    states = []
    for t in lst[:50]:
        if isinstance(t, dict):
            if "status" in t:
                statuses.append(str(t.get("status")))
            if "state" in t:
                states.append(str(t.get("state")))
    return {
        "tasks_available": bool(lst),
        "tasks_count": len(lst),
        "sample_status_values": sorted(set(statuses))[:10],
        "sample_state_values": sorted(set(states))[:10],
    }


def _format_uptime_de(seconds: Any) -> str | None:
    """Return a German human-readable uptime string like '5 Tage 3 Std 12 Min'."""
    if seconds is None:
        return None
    try:
        s = int(float(seconds))
    except Exception:
        return None
    if s < 0:
        s = 0

    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} Tage" if days != 1 else "1 Tag")
    if hours:
        parts.append(f"{hours} Std")
    if minutes or not parts:
        parts.append(f"{minutes} Min")

    return " ".join(parts)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coord: ProxmoxCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    # Device display name should be provided by coordinator (no IP).
    display = getattr(coord, "display_name", None) or coord.node or "PBS"

    device_info = DeviceInfo(
        identifiers={(DOMAIN, coord.device_identifier)},
        name=f"Proxmox {coord.backend.upper()} ({display})",
        manufacturer="Proxmox",
        model="Proxmox VE" if coord.backend == BACKEND_PVE else "Proxmox Backup Server",
    )

    if coord.backend == BACKEND_PVE:
        entities += [
            ProxmoxValueSensor(
                coord, entry,
                name="CPU Usage",
                key="cpu_percent",
                getter=lambda d: _cpu_to_percent((d.get("status") or {}).get("cpu")),
                unit=PERCENTAGE,
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:cpu-64-bit",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="Memory Usage",
                key="mem_percent",
                getter=lambda d: _mem_percent_from_status(d.get("status")),
                unit=PERCENTAGE,
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:memory",
                device_info=device_info,
            ),
            # ✅ Name without "(GiB)"
            ProxmoxValueSensor(
                coord, entry,
                name="Memory Used",
                key="mem_used_gib",
                getter=lambda d: _bytes_to_gib(((d.get("status") or {}).get("memory") or {}).get("used")),
                unit="GiB",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:memory",
                device_info=device_info,
            ),
            # ✅ Name without "(GiB)"
            ProxmoxValueSensor(
                coord, entry,
                name="Memory Total",
                key="mem_total_gib",
                getter=lambda d: _bytes_to_gib(((d.get("status") or {}).get("memory") or {}).get("total")),
                unit="GiB",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:memory",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="Load (1m)",
                key="load_1m",
                getter=lambda d: _load_1m((d.get("status") or {}).get("loadavg")),
                unit=None,
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:chart-line",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="Uptime",
                key="uptime",
                getter=lambda d: (d.get("status") or {}).get("uptime"),
                unit=UnitOfTime.SECONDS,
                device_class=SensorDeviceClass.DURATION,
                state_class=SensorStateClass.TOTAL_INCREASING,
                icon="mdi:clock-outline",
                device_info=device_info,
            ),
            ProxmoxTextSensor(
                coord, entry,
                name="Uptime (lesbar)",
                key="uptime_lesbar",
                getter=lambda d: _format_uptime_de((d.get("status") or {}).get("uptime")),
                icon="mdi:clock-outline",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="VMs Running",
                key="vms_running",
                getter=lambda d: (d.get("counts") or {}).get("vms_running"),
                unit=None,
                device_class=None,
                state_class=None,
                icon="mdi:server",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="VMs Total",
                key="vms_total",
                getter=lambda d: (d.get("counts") or {}).get("vms_total"),
                unit=None,
                device_class=None,
                state_class=None,
                icon="mdi:server",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="LXCs Running",
                key="lxcs_running",
                getter=lambda d: (d.get("counts") or {}).get("lxcs_running"),
                unit=None,
                device_class=None,
                state_class=None,
                icon="mdi:container",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="LXCs Total",
                key="lxcs_total",
                getter=lambda d: (d.get("counts") or {}).get("lxcs_total"),
                unit=None,
                device_class=None,
                state_class=None,
                icon="mdi:container",
                device_info=device_info,
            ),
        ]

    if coord.backend == BACKEND_PBS:
        entities += [
            ProxmoxValueSensor(
                coord, entry,
                name="CPU Usage",
                key="cpu_percent",
                getter=lambda d: _cpu_to_percent((d.get("status") or {}).get("cpu")),
                unit=PERCENTAGE,
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:cpu-64-bit",
                device_info=device_info,
            ),
            # ✅ Name without "(GiB)"
            ProxmoxValueSensor(
                coord, entry,
                name="Memory Used",
                key="mem_used_gib",
                getter=lambda d: _bytes_to_gib(((d.get("status") or {}).get("memory") or {}).get("used")),
                unit="GiB",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:memory",
                device_info=device_info,
            ),
            # ✅ Name without "(GiB)"
            ProxmoxValueSensor(
                coord, entry,
                name="Memory Total",
                key="mem_total_gib",
                getter=lambda d: _bytes_to_gib(((d.get("status") or {}).get("memory") or {}).get("total")),
                unit="GiB",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:memory",
                device_info=device_info,
            ),
            ProxmoxValueSensor(
                coord, entry,
                name="Uptime",
                key="uptime",
                getter=lambda d: (d.get("status") or {}).get("uptime"),
                unit=UnitOfTime.SECONDS,
                device_class=SensorDeviceClass.DURATION,
                state_class=SensorStateClass.TOTAL_INCREASING,
                icon="mdi:clock-outline",
                device_info=device_info,
            ),
            ProxmoxTextSensor(
                coord, entry,
                name="Uptime (lesbar)",
                key="uptime_lesbar",
                getter=lambda d: _format_uptime_de((d.get("status") or {}).get("uptime")),
                icon="mdi:clock-outline",
                device_info=device_info,
            ),
            ProxmoxTaskSensor(
                coord, entry,
                name="Running Tasks",
                key="running_tasks",
                getter=lambda d: _count_running_tasks(d.get("tasks_running")),
                icon="mdi:progress-clock",
                device_info=device_info,
                attrs_getter=lambda d: _tasks_debug_attrs(d.get("tasks_running")),
            ),
            ProxmoxTaskSensor(
                coord, entry,
                name="Failed Tasks (24h)",
                key="failed_tasks_24h",
                getter=lambda d: _count_failed_tasks_last_24h(d.get("tasks")),
                icon="mdi:alert-circle-outline",
                device_info=device_info,
                attrs_getter=lambda d: _tasks_debug_attrs(d.get("tasks")),
            ),
        ]

        for ds in (coord.data or {}).get("datastores", []) or []:
            store = ds.get("store")
            if not store:
                continue

            prefix = f"Datastore {store}"

            entities += [
                # ✅ Name without "(GiB)"
                DatastoreValueSensor(
                    coord, entry, store=store,
                    name=f"{prefix} Free",
                    key=f"ds:{store}:free_gib",
                    getter=lambda x: _bytes_to_gib(x.get("avail")),
                    unit="GiB",
                    device_class=None,
                    state_class=SensorStateClass.MEASUREMENT,
                    device_info=device_info,
                ),
                # ✅ Name without "(GiB)"
                DatastoreValueSensor(
                    coord, entry, store=store,
                    name=f"{prefix} Used",
                    key=f"ds:{store}:used_gib",
                    getter=lambda x: _bytes_to_gib(x.get("used")),
                    unit="GiB",
                    device_class=None,
                    state_class=SensorStateClass.MEASUREMENT,
                    device_info=device_info,
                ),
                # ✅ Name without "(GiB)"
                DatastoreValueSensor(
                    coord, entry, store=store,
                    name=f"{prefix} Total",
                    key=f"ds:{store}:total_gib",
                    getter=lambda x: _bytes_to_gib(x.get("total")),
                    unit="GiB",
                    device_class=None,
                    state_class=SensorStateClass.MEASUREMENT,
                    device_info=device_info,
                ),
                DatastoreValueSensor(
                    coord, entry, store=store,
                    name=f"{prefix} Usage",
                    key=f"ds:{store}:usage_percent",
                    getter=lambda x: _percent(x.get("used"), x.get("total")),
                    unit=PERCENTAGE,
                    device_class=None,
                    state_class=SensorStateClass.MEASUREMENT,
                    device_info=device_info,
                ),
            ]

    async_add_entities(entities)


class ProxmoxValueSensor(CoordinatorEntity[ProxmoxCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProxmoxCoordinator,
        entry: ConfigEntry,
        name: str,
        key: str,
        getter: Callable[[dict[str, Any]], Any],
        unit: Optional[str],
        device_class: Optional[SensorDeviceClass],
        state_class: Optional[SensorStateClass],
        icon: Optional[str],
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}:{coordinator.backend}:{key}"
        self._getter = getter

        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        if icon:
            self._attr_icon = icon

        self._attr_device_info = device_info

    @property
    def native_value(self) -> Any:
        return self._getter(self.coordinator.data or {})


class ProxmoxTextSensor(CoordinatorEntity[ProxmoxCoordinator], SensorEntity):
    """A sensor that returns a human-readable string (no unit/device_class)."""
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProxmoxCoordinator,
        entry: ConfigEntry,
        name: str,
        key: str,
        getter: Callable[[dict[str, Any]], str | None],
        icon: Optional[str],
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}:{coordinator.backend}:{key}"
        self._getter = getter

        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None
        if icon:
            self._attr_icon = icon

        self._attr_device_info = device_info

    @property
    def native_value(self) -> str | None:
        return self._getter(self.coordinator.data or {})


class ProxmoxTaskSensor(CoordinatorEntity[ProxmoxCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProxmoxCoordinator,
        entry: ConfigEntry,
        name: str,
        key: str,
        getter: Callable[[dict[str, Any]], int],
        icon: Optional[str],
        device_info: DeviceInfo,
        attrs_getter: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}:{coordinator.backend}:{key}"
        self._getter = getter
        self._attrs_getter = attrs_getter

        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None
        if icon:
            self._attr_icon = icon

        self._attr_device_info = device_info

    @property
    def native_value(self) -> int:
        val = self._getter(self.coordinator.data or {})
        return int(val) if val is not None else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs_getter(self.coordinator.data or {})


class DatastoreValueSensor(CoordinatorEntity[ProxmoxCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProxmoxCoordinator,
        entry: ConfigEntry,
        store: str,
        name: str,
        key: str,
        getter: Callable[[dict[str, Any]], Any],
        unit: Optional[str],
        device_class: Optional[SensorDeviceClass],
        state_class: Optional[SensorStateClass],
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.store = store
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}:pbs:{key}"
        self._getter = getter

        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_device_info = device_info

    @property
    def native_value(self) -> Any:
        for ds in (self.coordinator.data or {}).get("datastores", []) or []:
            if ds.get("store") == self.store:
                return self._getter(ds)
        return None