import asyncio, logging, os, time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import aiohttp
try:
    from aiohttp_socks import ProxyConnector, ProxyError, ProxyConnectionError, ProxyTimeoutError
    PROXY_ERRORS = (ProxyError, ProxyConnectionError, ProxyTimeoutError)
except ImportError:  # The dependency is optional until a SOCKS proxy is configured.
    ProxyConnector = None
    PROXY_ERRORS = ()
from .errors import ErrorClassifier, ErrorType, SelectelError
from app.config.regions import REGIONS

log = logging.getLogger(__name__)


class SelectelClient:
    # A proxy can occasionally drop a connection while the request itself is
    # still valid. Keep these retries local to the API call so the scheduler's
    # delay between subnets is not changed.
    NETWORK_RETRIES = 3
    NETWORK_RETRY_DELAY = 0.15

    def __init__(self, account, password: str, default_network_id: str | None = None, proxy: str | None = None, region: str | None = None, network_api_url: str | None = None, floating_network_id: str | None = None, api_timeout: float | None = None):
        self.account, self.password, self.region = account, password, region or account.region
        self.network_id = floating_network_id or default_network_id
        self.network_api_url = network_api_url or account.network_api_url
        self.proxy = proxy
        self.api_timeout = api_timeout or getattr(account, "api_timeout", 5.0)
        self.session: aiohttp.ClientSession | None = None; self.token: str | None = None
        self.socks_proxy = bool(proxy and proxy.lower().startswith(("socks4://", "socks4a://", "socks5://", "socks5h://")))
        self._request_lock = asyncio.Lock()
        self.token_expires_at = None
        self._last_request = 0.0

    async def open(self):
        if self.session is None or self.session.closed:
            if self.socks_proxy and ProxyConnector is None:
                raise SelectelError(ErrorType.UNKNOWN, "SOCKS proxy support is not installed; run pip install aiohttp-socks")
            # Reuse connections throughout an account cycle, including proxy tunnels.
            # The worker closes the session at the cycle boundary.
            connector_options = dict(limit=8, limit_per_host=2, enable_cleanup_closed=True, keepalive_timeout=15)
            connector = (ProxyConnector.from_url(self.proxy, **connector_options)
                         if self.socks_proxy else aiohttp.TCPConnector(**connector_options))
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(
                    total=self.api_timeout,
                    connect=float(os.getenv("API_CONNECT_TIMEOUT", str(self.api_timeout))),
                    sock_connect=float(os.getenv("API_CONNECT_TIMEOUT", str(self.api_timeout))),
                    sock_read=float(os.getenv("API_READ_TIMEOUT", str(self.api_timeout))),
                ),
            )

    async def _reset_connection(self):
        """Drop a possibly stale proxy connection before retrying."""
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _network_retry_delay(self, attempt: int):
        await asyncio.sleep(self.NETWORK_RETRY_DELAY)

    async def close(self):
        async with self._request_lock:
            await self._reset_connection()

    async def authenticate(self):
        url = self.account.auth_url or f"https://cloud.api.selcloud.ru/identity/v3/auth/tokens"
        body = {"auth": {"identity": {"methods": ["password"], "password": {"user": {"name": self.account.username, "domain": {"name": self.account.domain}, "password": self.password}}}, "scope": {"project": {"id": self.account.project_id}}}}
        for attempt in range(self.NETWORK_RETRIES):
            await self.open()
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
                    expires = payload.get("token", {}).get("expires_at")
                    self.token_expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp() if expires else None
                    return self.token
            except (aiohttp.ClientError, asyncio.TimeoutError, *PROXY_ERRORS) as exc:
                await self._reset_connection()
                if attempt + 1 < self.NETWORK_RETRIES:
                    log.debug("retrying Selectel authentication through proxy attempt=%s", attempt + 2)
                    await self._network_retry_delay(attempt)
                    continue
                raise SelectelError(ErrorType.NETWORK_ERROR, f"Selectel authentication network error ({type(exc).__name__})") from exc

    async def relogin(self):
        """Discard the cached Keystone token and perform a fresh login."""
        self.token = None
        token = await self.authenticate()
        log.info("Selectel re-authenticated account=%s", self.account.id)
        return token

    async def _request(self, method: str, path: str, region: str | None = None, **kwargs) -> Any:
        # UI checks and the hunter share a cached client. A connection reset
        # must not close a different request that is still in flight.
        async with self._request_lock:
            return await self._request_unlocked(method, path, region, **kwargs)

    async def _request_unlocked(self, method: str, path: str, region: str | None = None, **kwargs) -> Any:
        selected_region = region or self.region
        if selected_region not in REGIONS:
            raise ValueError(f"Unsupported region: {selected_region}")
        if not self.token or (self.token_expires_at and self.token_expires_at <= time.time() + 60):
            await self.authenticate()
        auth_retry = False
        network_attempt = 0
        while True:
            await self.open()
            # Rebuild headers on every attempt. After a 401 the token is
            # refreshed below and the retry must use the new token.
            headers = {"X-Auth-Token": self.token, "Content-Type": "application/json"}
            if self.proxy and not self.socks_proxy: kwargs["proxy"] = self.proxy
            try:
                interval = max(0.0, float(os.getenv("API_REQUEST_INTERVAL", "0.3")))
                await asyncio.sleep(max(0.0, self._last_request + interval - time.monotonic()))
                self._last_request = time.monotonic()
                endpoint = REGIONS[selected_region]["network_api_url"]
                async with self.session.request(method, f"{endpoint.rstrip('/')}/{path.lstrip('/')}", headers=headers, **kwargs) as response:
                    if response.status == 204:
                        return {}
                    try:
                        payload = await response.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError):
                        payload = {}
                    if response.status == 401 and not auth_retry:
                        # После изменения IAM-ролей старый Keystone token может
                        # продолжать жить, поэтому один раз перевыпускаем его.
                        await self.relogin()
                        auth_retry = True
                        continue
                    if response.status >= 400:
                        retry = response.headers.get("Retry-After")
                        neutron = payload.get("NeutronError", {}) if isinstance(payload, dict) else {}
                        reason = neutron.get("message") or payload.get("error", {}).get("message") or "Selectel API request failed"
                        error_type = neutron.get("type")
                        if error_type: reason = f"{error_type}: {reason}"
                        retry_seconds = None
                        if retry:
                            try:
                                retry_seconds = max(0.0, float(retry))
                            except ValueError:
                                retry_seconds = max(0.0, parsedate_to_datetime(retry).timestamp() - time.time())
                        # Resource POSTs retry in the next scheduled cycle, not inside this call.
                        if method.upper() in {"GET", "HEAD", "DELETE"} and (response.status == 429 or response.status >= 500) and network_attempt + 1 < self.NETWORK_RETRIES:
                            network_attempt += 1
                            # The ordinary API_REQUEST_INTERVAL applies on every retry.
                            continue
                        raise SelectelError(ErrorClassifier.classify(response.status, payload), reason, retry_seconds, response.status)
                    return payload
            except (aiohttp.ClientError, asyncio.TimeoutError, *PROXY_ERRORS) as exc:
                await self._reset_connection()
                network_attempt += 1
                # POST is not idempotent: a lost response may already have
                # allocated an IP. Only retry it when connection setup failed.
                before_send = isinstance(exc, (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError, *PROXY_ERRORS))
                safe_retry = method.upper() in ("GET", "HEAD", "OPTIONS", "DELETE") or before_send
                if safe_retry and network_attempt < self.NETWORK_RETRIES:
                    log.debug(
                        "retrying Selectel API request through proxy path=%s attempt=%s",
                        path, network_attempt + 1,
                    )
                    await self._network_retry_delay(network_attempt - 1)
                    continue
                raise SelectelError(ErrorType.NETWORK_ERROR, f"Selectel API network error ({type(exc).__name__})") from exc

    async def validate_account(self): return await self._request("GET", "floatingip_pools")

    async def create_floating_ip(self, region: str, subnet_id: str | None = None, *, description=None, network_id=None):
        if subnet_id is None:
            subnet_id, region = region, self.region
        region_config = REGIONS[region]
        network_id = network_id or region_config["floating_network_id"]
        body = {"floating_network_id": network_id, "subnet_id": subnet_id}
        if description:
            body["description"] = description
        try:
            return (await self._request("POST", "floatingips", region=region, json={"floatingip": body})).get("floatingip", {})
        except SelectelError as exc:
            raise SelectelError(exc.kind, f"{exc}; region={region}; floating_network_id={region_config['floating_network_id']}; subnet_id={subnet_id}", exc.retry_after, exc.status) from exc

    async def get_floating_ips(self, region=None): return await self._request("GET", "floatingips", region=region)
    async def get_floatingip_pools(self, region=None): return await self._request("GET", "floatingip_pools", region=region)
    async def get_floating_ip(self, fip_id, region): return (await self._request("GET", f"floatingips/{fip_id}", region=region)).get("floatingip", {})
    async def delete_floating_ip(self, fip_id: str, region=None): return await self._request("DELETE", f"floatingips/{fip_id}", region=region)

    async def resolve_network(self, region, subnet_id):
        # The network belongs to the region, independently of the selected subnet.
        return REGIONS[region]["floating_network_id"]
