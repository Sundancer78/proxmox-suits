from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_BACKEND,
    CONF_HOST,
)
from .coordinator import ProxmoxCoordinator


def _device_meta(entry: ConfigEntry) -> tuple[str, str, str]:
    backend = entry.data[CONF_BACKEND]
    host = entry.data[CONF_HOST]

    if backend == "pve":
        model = "Proxmox VE"
    else:
        model = "Proxmox Backup Server"

    name = f"Proxmox {backend.upper()} ({host})"
    manufacturer = "Proxmox"
    return name, manufacturer, model


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ProxmoxCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Create/update a Device so HA groups entities like in your WeatherDuino screenshot
    dev_reg = dr.async_get(hass)
    name, manufacturer, model = _device_meta(entry)

    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.device_identifier)},
        name=name,
        manufacturer=manufacturer,
        model=model,
    )

    # If version info is available, store it as sw_version on the device
    ver = None
    vdata = (coordinator.data or {}).get("version")
    if isinstance(vdata, dict):
        # PVE/PBS often returns {"version": "..."} plus other fields
        ver = vdata.get("version") or vdata.get("release")
    elif isinstance(vdata, str):
        ver = vdata

    if ver:
        dev_reg.async_update_device(device.id, sw_version=str(ver))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
