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

        # Stable identifier (do NOT change this for display reasons)
        self.device_identifier = f"{self.backend}:{self.host}:{self.port}"

        # UI name (never IP)
        self.display_name: str = ""

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
        try:
            return await self.api.get(path, params=params)
        except ProxmoxApiError as e:
            _LOGGER.debug("API call failed (%s): %s", path, e)
            return default

    @staticmethod
    def _extract_hostname_from_status(status: Any) -> str | None:
        """Try to extract a nice hostname/nodename from a PBS status payload."""
        if not isinstance(status, dict):
            return None

        for key in ("hostname", "nodename", "node", "name"):
            v = status.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

        node = status.get("node")
        if isinstance(node, dict):
            for key in ("hostname", "nodename", "name"):
                v = node.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        return None

    def _set_display_name_no_ip(self, *, pve_node: str | None = None, pbs_hostname: str | None = None) -> None:
        """Enforce no-IP display naming."""
        if self.backend == BACKEND_PVE:
            # For PVE: prefer node
            self.display_name = (pve_node or self.node or "PVE").strip()
            return

        # For PBS: prefer real hostname, fallback to PBS (never host/ip)
        hn = (pbs_hostname or "").strip()
        self.display_name = hn if hn else "PBS"

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            version = await self._safe_get("/version", default=None)

            if self.backend == BACKEND_PVE:
                if not self.node:
                    nodes = await self._safe_get("/nodes", default=[])
                    self.node = (nodes[0].get("node") if nodes else "") or ""
                if not self.node:
                    raise UpdateFailed("Could not determine PVE node name")

                status = await self._safe_get(f"/nodes/{self.node}/status", default={})

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

                tasks_running = await self._safe_get(
                    f"/nodes/{self.node}/tasks",
                    params={"running": "true", "limit": 200},
                    default=[],
                )

                self._set_display_name_no_ip(pve_node=self.node)

                return {
                    "backend": BACKEND_PVE,
                    "node": self.node,
                    "display_name": self.display_name,
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

            status = await self._safe_get(f"/nodes/{node}/status", default={})
            datastores = await self._safe_get("/status/datastore-usage", default=[])

            tasks_all = await self._safe_get(
                f"/nodes/{node}/tasks",
                params={"limit": 200},
                default=[],
            )

            tasks_running = await self._safe_get(
                f"/nodes/{node}/tasks",
                params={"running": "true", "limit": 200},
                default=[],
            )

            if not status and not datastores and not tasks_all and not tasks_running:
                raise UpdateFailed("PBS: all API calls failed (no data returned)")

            hostname = self._extract_hostname_from_status(status)
            self._set_display_name_no_ip(pbs_hostname=hostname)

            return {
                "backend": BACKEND_PBS,
                "node": node,
                "display_name": self.display_name,
                "version": version,
                "status": status or {},
                "datastores": datastores or [],
                "tasks": tasks_all or [],
                "tasks_running": tasks_running or [],
            }

        except UpdateFailed:
            raise
        except Exception as e:
            raise UpdateFailed(str(e)) from e