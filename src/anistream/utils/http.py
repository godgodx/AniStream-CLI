from __future__ import annotations

import threading
from collections.abc import Mapping
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


class HttpClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        cookie: str = "",
        cookie_hosts: set[str] | None = None,
        timeout: tuple[float, float] = (10.0, 30.0),
    ) -> None:
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.cookie = cookie.strip()
        self.cookie_hosts = {host.lower() for host in (cookie_hosts or set())}
        self.timeout = timeout
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            retry = Retry(
                total=2,
                connect=2,
                read=2,
                backoff_factor=0.5,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "HEAD"}),
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
            session = requests.Session()
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._local.session = session
        return session

    def headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": "en-US,en;q=0.8,fr;q=0.6",
        }
        if extra:
            headers.update(extra)
        return headers

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        supplied = kwargs.pop("headers", None)
        headers = self.headers(supplied if isinstance(supplied, Mapping) else None)
        host = (urlparse(url).hostname or "").lower()
        if self.cookie and host in self.cookie_hosts and "Cookie" not in headers:
            headers["Cookie"] = self.cookie
        kwargs.setdefault("timeout", self.timeout)
        response = self._session().request(method, url, headers=headers, **kwargs)
        return response

    def get(self, url: str, **kwargs: object) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: object) -> requests.Response:
        return self.request("POST", url, **kwargs)
