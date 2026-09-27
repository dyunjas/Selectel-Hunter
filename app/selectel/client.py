import asyncio, logging
from typing import Any
import aiohttp
from .errors import ErrorClassifier, ErrorType, SelectelError

log = logging.getLogger(__name__)


class SelectelClient:
    def __init__(self, account, password: str, default_network_id: str, proxy: str | None = None):
        self.account, self.password, self.network_id, self.proxy = account, password, default_network_id, proxy
        self.session: aiohttp.ClientSession | None = None; self.token: str | None = None

    async def open(self):
        if self.session is None: self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30, connect=10))

    async def close(self):
        if self.session: await self.session.close(); self.session = None

    async def authenticate(self):
        await self.open()
        url = self.account.auth_url or f"https://cloud.api.selcloud.ru/identity/v3/auth/tokens"
        body = {"auth": {"identity": {"methods": ["password"], "password": {"user": {"name": self.account.username, "domain": {"name": self.account.domain}, "password": self.password}}}, "scope": {"project": {"id": self.account.project_id}}}}
        request_kwargs = {"json": body}
        if self.proxy: request_kwargs["proxy"] = self.proxy
        try:
            async with self.session.post(url, **request_kwargs) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    neutron = payload.get("NeutronError", {}) if isinstance(payload, dict) else {}
                    reason = neutron.get("message") or payload.get("error", {}).get("message") or "Authentication failed"
                    raise SelectelError(ErrorClassifier.classify(response.status, payload), reason, status=response.status)
                self.token = response.headers.get("X-Subject-Token") or payload.get("token", {}).get("id")
                if not self.token: raise SelectelError(ErrorType.AUTH_ERROR, "Authentication token missing")
                return self.token
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise SelectelError(ErrorType.NETWORK_ERROR, "Selectel authentication network error") from exc

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        await self.open()
        if not self.token: await self.authenticate()
        headers = {"X-Auth-Token": self.token, "Content-Type": "application/json"}
        for attempt in range(2):
            if self.proxy: kwargs["proxy"] = self.proxy
            try:
                async with self.session.request(method, f"{self.account.network_api_url.rstrip('/')}/{path.lstrip('/')}", headers=headers, **kwargs) as response:
                    payload = await response.json(content_type=None)
                    if response.status == 401 and attempt == 0: self.token = None; await self.authenticate(); continue
                    if response.status >= 400:
                        retry = response.headers.get("Retry-After")
                        neutron = payload.get("NeutronError", {}) if isinstance(payload, dict) else {}
                        reason = neutron.get("message") or payload.get("error", {}).get("message") or "Selectel API request failed"
                        error_type = neutron.get("type")
                        if error_type: reason = f"{error_type}: {reason}"
                        raise SelectelError(ErrorClassifier.classify(response.status, payload), reason, float(retry) if retry else None, response.status)
                    return payload
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise SelectelError(ErrorType.NETWORK_ERROR, "Selectel API network error") from exc

    async def validate_account(self): return await self._request("GET", "floatingip_pools")

    async def create_floating_ip(self, subnet_id: str):
        return (await self._request("POST", "floatingips", json={"floatingip": {"floating_network_id": self.network_id, "subnet_id": subnet_id}})).get("floatingip", {})

    async def get_floating_ips(self): return await self._request("GET", "floatingips")
    async def get_floatingip_pools(self): return await self._request("GET", "floatingip_pools")
    async def delete_floating_ip(self, fip_id: str): return await self._request("DELETE", f"floatingips/{fip_id}")
