"""Platform checkers: fetch -> parse -> classify for one input row.

To add another platform, subclass PlatformChecker and implement the four
abstract methods; the runner, report and CLI work with any checker.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from .classifier import Classification, classify
from .fetcher import FetchSettings, HttpFetcher
from .models import CheckResult, FetchResult, InputRow, PageSignals, Verdict
from .parser import parse_page
from .rules import Marketplace, Rules

_GTI_RE = re.compile(r"amzn1\.dv\.gti\.[0-9a-f-]{36}", re.IGNORECASE)
_ASIN_RE = re.compile(r"(?:^|/)(B0[A-Z0-9]{8}|[0-9]{9}[0-9X])(?:[/?#]|$)")


class PlatformChecker(ABC):
    name: str = "base"

    @abstractmethod
    def build_url(self, row: InputRow) -> str:
        """Normalize the row's link into the URL we will actually fetch."""

    @abstractmethod
    async def fetch(self, url: str) -> FetchResult: ...

    @abstractmethod
    def parse(self, fetch: FetchResult) -> PageSignals: ...

    @abstractmethod
    def classify(self, row: InputRow, fetch: FetchResult, page: PageSignals) -> Classification: ...

    async def __aenter__(self) -> PlatformChecker:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def on_result(self, result: CheckResult) -> None:
        """Hook called after each row, e.g. to slow down after a block."""
        return None

    async def check(self, row: InputRow) -> tuple[CheckResult, FetchResult]:
        url = self.build_url(row)
        fetch = await self.fetch(url)
        return self.evaluate(row, fetch), fetch

    def evaluate(self, row: InputRow, fetch: FetchResult) -> CheckResult:
        """Parse + classify an already-fetched page (also used by `reclassify`)."""
        page = self.parse(fetch)
        outcome = self.classify(row, fetch, page)
        return CheckResult(
            row=row,
            verdict=outcome.verdict,
            http_status=fetch.http_status,
            final_url=fetch.final_url,
            requested_url=fetch.requested_url,
            resolved_asin=page.resolved_asin,
            resolved_gti=page.resolved_gti,
            page_title=page.page_title,
            offers=page.offers,
            matched_signals=outcome.signals,
            checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
            fetch_method=fetch.method,
            error=outcome.error or fetch.error,
        )


class PrimeVideoChecker(PlatformChecker):
    name = "prime_video"

    def __init__(
        self,
        rules: Rules,
        marketplace: Marketplace,
        settings: FetchSettings | None = None,
    ) -> None:
        self.rules = rules
        self.marketplace = marketplace
        self.settings = settings or FetchSettings()
        self._fetcher: HttpFetcher | None = None

    async def __aenter__(self) -> PrimeVideoChecker:
        self._fetcher = HttpFetcher(self.marketplace, self.settings)
        await self._fetcher.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._fetcher is not None:
            await self._fetcher.__aexit__(*exc_info)

    @property
    def fetcher(self) -> HttpFetcher:
        if self._fetcher is None:
            raise RuntimeError("PrimeVideoChecker must be used as an async context manager")
        return self._fetcher

    def build_url(self, row: InputRow) -> str:
        """Rewrite any GTI/ASIN link onto the configured marketplace host.

        Accepts a bare ASIN or GTI, amazon.*/dp/<ASIN>, amazon.*/gp/video/detail/<id>
        and primevideo.com/detail/<id>. Unrecognized links are fetched as-is.
        """
        source = row.amazon_url.strip() or row.content_id.strip()
        title_id = self.extract_title_id(source)
        if title_id is None:
            return source
        return f"https://{self.marketplace.host}/gp/video/detail/{title_id}"

    @staticmethod
    def extract_title_id(link: str) -> str | None:
        gti = _GTI_RE.search(link)
        if gti is not None:
            return gti.group(0)
        asin = _ASIN_RE.search(link.strip())
        if asin is not None:
            return asin.group(1)
        return None

    def on_result(self, result: CheckResult) -> None:
        if result.verdict is Verdict.BLOCKED:
            self.fetcher.pacer.penalize()
        else:
            self.fetcher.pacer.reward()

    async def fetch(self, url: str) -> FetchResult:
        return await self.fetcher.fetch(url)

    def parse(self, fetch: FetchResult) -> PageSignals:
        return parse_page(fetch, self.rules)

    def classify(self, row: InputRow, fetch: FetchResult, page: PageSignals) -> Classification:
        return classify(row, fetch, page, self.rules, self.marketplace)
