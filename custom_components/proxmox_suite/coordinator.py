from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ProxmoxAPI, ProxmoxApiError
from .const import (
    BACKEND_PBS,
    BACKEND_PVE,
    CONF_BACKEND,
    CONF_HOST,
    CONF_NODE,
    CONF_PORT,
    CONF_TOKEN_ID,
    CONF_TOKEN_SECRET,
    CONF_VERIFY_SSL,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class ProxmoxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.backend = entry.data[CONF_BACKEND]
        self.host = entry.data[CONF_HOST]
        self.port = entry.data[CONF_PORT]
        self.node = entry.data.get(CONF_NODE) or ("localhost" if self.backend == BACKEND_PBS else "")
        self.verify_ssl = entry.data[CONF_VERIFY_SSL]

        self.device_identifier = f"{self.backend}:{self.host}:{self.port}"

        token_id = entry.data[CONF_TOKEN_ID]
        token_secret = entry.data[CONF_TOKEN_SECRET]

        base_url = f"https://{self.host}:{self.port}/api2/json"

        if self.backend == BACKEND_PVE:
            headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        else:
            headers = {"Authorization": f"PBSAPIToken {token_id}:{token_secret}"}

        session = async_get_clientsession(hass)
        self.api = ProxmoxAPI(base_url=base_url, headers=headers, session=session, verify_ssl=self.verify_ssl)

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=f"proxmox_suite_{entry.entry_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

    async def _safe_get(self, path: str, *, params: dict[str, Any] | None = None, default: Any = None) -> Any:
        """Fetch a Proxmox endpoint; on error return default (does NOT fail the coordinator)."""
        try:
            return await self.api.get(path, params=params)
        except ProxmoxApiError as e:
            _LOGGER.debug("API call failed (%s): %s", path, e)
            return default

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            # version is optional
            version = await self._safe_get("/version", default=None)

            if self.backend == BACKEND_PVE:
                # Determine node if missing
                if not self.node:
                    nodes = await self._safe_get("/nodes", default=[])
                    self.node = (nodes[0].get("node") if nodes else "") or ""
                if not self.node:
                    raise UpdateFailed("Could not determine PVE node name")

                status = await self._safe_get(f"/nodes/{self.node}/status", default={})

                # VM/LXC counts (best effort)
                vms_total = vms_running = 0
                lxcs_total = lxcs_running = 0

                vms = await self._safe_get(f"/nodes/{self.node}/qemu", default=[])
                if isinstance(vms, list):
                    vms_total = len(vms)
                    vms_running = sum(1 for vm in vms if vm.get("status") == "running")

                lxcs = await self._safe_get(f"/nodes/{self.node}/lxc", default=[])
                if isinstance(lxcs, list):
                    lxcs_total = len(lxcs)
                    lxcs_running = sum(1 for ct in lxcs if ct.get("status") == "running")

                # Optional running tasks for PVE (best effort)
                tasks_running = await self._safe_get(
                    f"/nodes/{self.node}/tasks",
                    params={"running": "true", "limit": 200}
,
                    default=[],
                )

                return {
                    "backend": BACKEND_PVE,
                    "node": self.node,
                    "version": version,
                    "status": status or {},
                    "counts": {
                        "vms_total": vms_total,
                        "vms_running": vms_running,
                        "lxcs_total": lxcs_total,
                        "lxcs_running": lxcs_running,
                    },
                    "tasks_running": tasks_running or [],
                }

            # ======================
            # PBS
            # ======================
            node = self.node or "localhost"

            # These are best-effort too: if one fails, we still update with what we have.
            status = await self._safe_get(f"/nodes/{node}/status", default={})
            datastores = await self._safe_get("/status/datastore-usage", default=[])

            # All tasks (raise limit so failures aren't truncated)
            tasks_all = await self._safe_get(
                f"/nodes/{node}/tasks",
                params={"limit": 200},
                default=[],
            )

            # Running tasks filtered (may be empty if no running tasks or endpoint ignores filter)
            tasks_running = await self._safe_get(
                f"/nodes/{node}/tasks",
                params={"running": "true", "limit": 200},
                default=[],
            )

            # If everything is empty AND status/datastores empty, likely no connectivity → fail coordinator
            # (prevents "false green" when PBS is completely unreachable)
            if not status and not datastores and not tasks_all and not tasks_running:
                raise UpdateFailed("PBS: all API calls failed (no data returned)")

            return {
                "backend": BACKEND_PBS,
                "node": node,
                "version": version,
                "status": status or {},
                "datastores": datastores or [],
                "tasks": tasks_all or [],
                "tasks_running": tasks_running or [],
            }

        except UpdateFailed:
            raise
        except Exception as e:
            # Any unexpected exception should mark update failed (and show in logs)
            raise UpdateFailed(str(e)) from e
