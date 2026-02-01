from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .coordinator import ProxmoxCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ProxmoxCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # ✅ Title in "Geräte & Dienste" ohne IP
    display = coordinator.display_name or ("PVE" if coordinator.backend == "pve" else "PBS")
    new_title = f"Proxmox {coordinator.backend.upper()} ({display})"
    if entry.title != new_title:
        hass.config_entries.async_update_entry(entry, title=new_title)

    # ✅ Device Registry Name ohne IP
    dev_reg = dr.async_get(hass)
    model = "Proxmox VE" if coordinator.backend == "pve" else "Proxmox Backup Server"
    device_name = f"Proxmox {coordinator.backend.upper()} ({display})"

    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.device_identifier)},
        name=device_name,
        manufacturer="Proxmox",
        model=model,
    )

    # Optional: software version
    ver = None
    vdata = (coordinator.data or {}).get("version")
    if isinstance(vdata, dict):
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