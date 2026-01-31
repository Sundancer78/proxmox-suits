from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ProxmoxAPI, ProxmoxApiError
from .const import (
    DOMAIN,
    CONF_BACKEND,
    CONF_HOST,
    CONF_PORT,
    CONF_TOKEN_ID,
    CONF_TOKEN_SECRET,
    CONF_VERIFY_SSL,
    CONF_NODE,
    BACKEND_PVE,
    BACKEND_PBS,
    DEFAULT_PVE_PORT,
    DEFAULT_PBS_PORT,
    DEFAULT_VERIFY_SSL,
)


async def _validate(hass: HomeAssistant, data: dict) -> dict:
    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}/api2/json"
    session = async_get_clientsession(hass)

    backend = data[CONF_BACKEND]
    token_id = data[CONF_TOKEN_ID]
    token_secret = data[CONF_TOKEN_SECRET]

    if backend == BACKEND_PVE:
        headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
    else:
        headers = {"Authorization": f"PBSAPIToken {token_id}:{token_secret}"}

    api = ProxmoxAPI(base_url=base_url, headers=headers, session=session, verify_ssl=data[CONF_VERIFY_SSL])

    # minimal check
    if backend == BACKEND_PVE:
        nodes = await api.get("/nodes")
        if not nodes:
            raise ProxmoxApiError("No nodes returned")
        node = (data.get(CONF_NODE) or "").strip() or (nodes[0].get("node") or "")
        if not node:
            raise ProxmoxApiError("Could not determine node")
        return {"node": node}
    else:
        # PBS: /version exists
        _ver = await api.get("/version")
        node = (data.get(CONF_NODE) or "").strip() or "localhost"
        return {"node": node}


class ProxmoxSuiteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            schema = vol.Schema(
                {vol.Required(CONF_BACKEND, default=BACKEND_PVE): vol.In([BACKEND_PVE, BACKEND_PBS])}
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        self._backend = user_input[CONF_BACKEND]
        return await self.async_step_connection()

    async def async_step_connection(self, user_input=None):
        errors = {}
        backend = getattr(self, "_backend", BACKEND_PVE)

        default_port = DEFAULT_PVE_PORT if backend == BACKEND_PVE else DEFAULT_PBS_PORT
        default_node = "" if backend == BACKEND_PVE else "localhost"

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=default_port): int,
                    vol.Required(CONF_TOKEN_ID): str,
                    vol.Required(CONF_TOKEN_SECRET): str,
                    vol.Optional(CONF_NODE, default=default_node): str,
                    vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                }
            )
            return self.async_show_form(step_id="connection", data_schema=schema, errors=errors)

        # attach backend
        data = dict(user_input)
        data[CONF_BACKEND] = backend

        try:
            validated = await _validate(self.hass, data)
        except Exception:
            errors["base"] = "cannot_connect"
            schema = vol.Schema(
                {
                    vol.Required(CONF_HOST, default=user_input[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=user_input[CONF_PORT]): int,
                    vol.Required(CONF_TOKEN_ID, default=user_input[CONF_TOKEN_ID]): str,
                    vol.Required(CONF_TOKEN_SECRET, default=user_input[CONF_TOKEN_SECRET]): str,
                    vol.Optional(CONF_NODE, default=user_input.get(CONF_NODE, default_node)): str,
                    vol.Required(CONF_VERIFY_SSL, default=user_input[CONF_VERIFY_SSL]): bool,
                }
            )
            return self.async_show_form(step_id="connection", data_schema=schema, errors=errors)

        node = validated["node"]
        data[CONF_NODE] = node

        # unique id separates pve/pbs entries cleanly
        unique_id = f"{backend}:{data[CONF_HOST]}:{data[CONF_PORT]}:{node}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        title = f"Proxmox {backend.upper()} ({data[CONF_HOST]})"
        return self.async_create_entry(title=title, data=data)
