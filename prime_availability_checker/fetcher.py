"""HTTP fetching with polite pacing, retries and block back-off.

Step 0 showed that Prime Video detail pages are server-rendered: the buy box
is in the initial HTML's hydration JSON. So plain httpx is enough and there is
no headless-browser fallback. If Amazon ever moves offers to client-side
rendering, pages will come back without hydration JSON and be classified
UNKNOWN ("no_hydration_json") rather than guessed; that is the cue to add a
browser fetcher implementing the same `fetch(url) -> FetchResult` method.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

import httpx

from .models import FetchResult
from .rules import Marketplace

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class FetchSettings:
    concurrency: int = 3
    # Random gap between the *start* of consecutive requests, across all workers.
    delay_min: float = 1.5
    delay_max: float = 4.0
    timeout: float = 30.0
    max_attempts: int = 4
    backoff_base: float = 2.0
    # After a BLOCKED page: pause everyone, then keep a slower pace for a while.
    block_cooldown: float = 60.0
    block_slowdown: float = 2.0
    max_slowdown: float = 8.0
    proxy: str | None = None


class Pacer:
    """Spaces request starts by a random delay, shared by all workers.

    `penalize()` is called when we get blocked: it pauses all new requests for
    the cooldown and multiplies the delay. Each clean response decays the
    multiplier back towards 1.
    """

    def __init__(self, settings: FetchSettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._next_start = 0.0
        self._slowdown = 1.0

    @property
    def slowdown(self) -> float:
        return self._slowdown

    async def wait_turn(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next_start > now:
                await asyncio.sleep(self._next_start - now)
            gap = random.uniform(self._settings.delay_min, self._settings.delay_max)
            self._next_start = time.monotonic() + gap * self._slowdown

    def penalize(self) -> None:
        settings = self._settings
        self._slowdown = min(self._slowdown * settings.block_slowdown, settings.max_slowdown)
        resume_at = time.monotonic() + settings.block_cooldown
        self._next_start = max(self._next_start, resume_at)
        log.warning(
            "Blocked: pausing %.0fs, slowdown now x%.1f",
            settings.block_cooldown,
            self._slowdown,
        )

    def reward(self) -> None:
        if self._slowdown > 1.0:
            self._slowdown = max(1.0, self._slowdown * 0.9)


class HttpFetcher:
    method = "http"

    def __init__(self, marketplace: Marketplace, settings: FetchSettings) -> None:
        self.marketplace = marketplace
        self.settings = settings
        self.pacer = Pacer(settings)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HttpFetcher:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self.marketplace.accept_language,
            "Accept-Encoding": "gzip, deflate",
            "Upgrade-Insecure-Requests": "1",
        }
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=self.settings.timeout,
            follow_redirects=True,
            proxy=self.settings.proxy,
            limits=httpx.Limits(max_connections=self.settings.concurrency),
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def fetch(self, url: str) -> FetchResult:
        if self._client is None:
            raise RuntimeError("HttpFetcher must be used as an async context manager")

        last_error: str | None = None
        last_response: httpx.Response | None = None
        attempts = 0

        for attempt in range(1, self.settings.max_attempts + 1):
            attempts = attempt
            await self.pacer.wait_turn()
            try:
                response = await self._client.get(url)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"[:300]
                last_response = None
                log.info("Attempt %d for %s failed: %s", attempt, url, last_error)
            else:
                last_response = response
                last_error = None
                if response.status_code < 500:
                    break
                log.info("Attempt %d for %s got HTTP %d", attempt, url, response.status_code)

            if attempt < self.settings.max_attempts:
                backoff = self.settings.backoff_base ** attempt + random.uniform(0, 1)
                await asyncio.sleep(backoff)

        if last_response is None:
            return FetchResult(
                requested_url=url,
                final_url=None,
                http_status=None,
                html=None,
                method=self.method,
                attempts=attempts,
                error=last_error,
            )

        return FetchResult(
            requested_url=url,
            final_url=str(last_response.url),
            http_status=last_response.status_code,
            html=last_response.text,
            method=self.method,
            redirects=len(last_response.history),
            attempts=attempts,
        )
