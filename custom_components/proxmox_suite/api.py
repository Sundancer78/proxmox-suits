from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


class ProxmoxApiError(Exception):
    pass


@dataclass
class ProxmoxAPI:
    base_url: str
    headers: dict[str, str]
    session: aiohttp.ClientSession
    verify_ssl: bool

    async def get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        ssl = self.verify_ssl  # bool; False erlaubt self-signed

        try:
            async with self.session.get(url, headers=self.headers, params=params, ssl=ssl, timeout=20) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise ProxmoxApiError(f"HTTP {resp.status} for {path}: {text}")
                payload = await resp.json(content_type=None)
                return payload.get("data")
        except aiohttp.ClientError as e:
            raise ProxmoxApiError(str(e)) from e