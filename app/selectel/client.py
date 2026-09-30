import asyncio, logging
from typing import Any
import aiohttp
try:
    from aiohttp_socks import ProxyConnector
except ImportError:  # The dependency is optional until a SOCKS proxy is configured.
    ProxyConnector = None
from .errors import ErrorClassifier, ErrorType, SelectelError
from app.config.regions import REGIONS

log = logging.getLogger(__name__)


class SelectelClient:
    def __init__(self, account, password: str, default_network_id: str | None = None, proxy: str | None = None, region: str | None = None, network_api_url: str | None = None, floating_network_id: str | None = None, api_timeout: float | None = None):
        self.account, self.password, self.region = account, password, region or account.region
        self.network_id = floating_network_id or default_network_id
        self.network_api_url = network_api_url or account.network_api_url
        self.proxy = proxy
        self.api_timeout = api_timeout or getattr(account, "api_timeout", 5.0)
        self.session: aiohttp.ClientSession | None = None; self.token: str | None = None
        self.socks_proxy = bool(proxy and proxy.lower().startswith(("socks4://", "socks4a://", "socks5://", "socks5h://")))

    async def open(self):
        if self.session is None:
            if self.socks_proxy and ProxyConnector is None:
                raise SelectelError(ErrorType.UNKNOWN, "SOCKS proxy support is not installed; run pip install aiohttp-socks")
            connector = ProxyConnector.from_url(self.proxy) if self.socks_proxy else None
            self.session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=self.api_timeout, connect=self.api_timeout))

    async def close(self):
        if self.session: await self.session.close(); self.session = None

    async def authenticate(self):
        await self.open()
        url = self.account.auth_url or f"https://cloud.api.selcloud.ru/identity/v3/auth/tokens"
        body = {"auth": {"identity": {"methods": ["password"], "password": {"user": {"name": self.account.username, "domain": {"name": self.account.domain}, "password": self.password}}}, "scope": {"project": {"id": self.account.project_id}}}}
        request_kwargs = {"json": body}
        if self.proxy and not self.socks_proxy: request_kwargs["proxy"] = self.proxy
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

    async def _request(self, method: str, path: str, region: str | None = None, **kwargs) -> Any:
        await self.open()
        if not self.token: await self.authenticate()
        headers = {"X-Auth-Token": self.token, "Content-Type": "application/json"}
        for attempt in range(2):
            if self.proxy and not self.socks_proxy: kwargs["proxy"] = self.proxy
            try:
                endpoint = REGIONS.get(region or self.region, REGIONS.get(self.region, REGIONS["ru-3"]))["network_api_url"]
                async with self.session.request(method, f"{endpoint.rstrip('/')}/{path.lstrip('/')}", headers=headers, **kwargs) as response:
                    payload = await response.json(content_type=None)
                    if response.status == 401 and attempt == 0:
                        # После изменения IAM-ролей старый Keystone token может
                        # продолжать жить, поэтому один раз перевыпускаем его.
                        self.token = None
                        await self.authenticate()
                        continue
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

    async def create_floating_ip(self, region: str, subnet_id: str | None = None):
        if subnet_id is None:
            subnet_id, region = region, self.region
        region_config = REGIONS[region]
        try:
            return (await self._request("POST", "floatingips", region=region, json={"floatingip": {"floating_network_id": region_config["floating_network_id"], "subnet_id": subnet_id}})).get("floatingip", {})
        except SelectelError as exc:
            raise SelectelError(exc.kind, f"{exc}; region={region}; floating_network_id={region_config['floating_network_id']}; subnet_id={subnet_id}", exc.retry_after, exc.status) from exc

    async def get_floating_ips(self): return await self._request("GET", "floatingips")
    async def get_floatingip_pools(self): return await self._request("GET", "floatingip_pools")
    async def delete_floating_ip(self, fip_id: str): return await self._request("DELETE", f"floatingips/{fip_id}")
