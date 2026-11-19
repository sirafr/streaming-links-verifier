"""Data types shared by the fetcher, parser, classifier and report writer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Verdict(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    REDIRECTED = "REDIRECTED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class PageKind(StrEnum):
    """What kind of page the fetch produced, before looking at offers."""

    DETAIL = "detail"          # a detail page with embedded hydration JSON
    NOT_FOUND = "not_found"    # 404 / "Page Not Found" / Dogs of Amazon
    BLOCKED = "blocked"        # captcha, throttling, 403/429/503
    FETCH_FAILED = "fetch_failed"  # timeout / connection error after retries
    UNRECOGNIZED = "unrecognized"  # loaded, but not a page we know how to read


@dataclass(slots=True)
class InputRow:
    content_id: str
    title: str
    amazon_url: str
    # Every other column from the alert file, passed through to the report.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Offer:
    type: str                      # prime | channel | buy | rent | free | play
    price: str | None = None       # "£7.99", or "included" for prime
    provider: str | None = None    # channel name, e.g. "Hayu"
    scope: str | None = None       # EPISODE | SEASON | SEASON_PASS | MOVIE ...
    quality: str | None = None     # SD | HD | UHD
    label: str | None = None       # raw button label, for auditing

    def short(self) -> str:
        """Compact form for the CSV column, e.g. 'buy:£7.99 (SEASON SD)'."""
        text = self.type
        if self.provider:
            text += f":{self.provider}"
        if self.price:
            text += f":{self.price}"
        qualifiers = [part for part in (self.scope, self.quality) if part]
        if qualifiers:
            text += f" ({' '.join(qualifiers)})"
        return text

    def key(self) -> tuple[str | None, ...]:
        return (self.type, self.provider, self.price, self.scope, self.quality)


@dataclass(slots=True)
class FetchResult:
    requested_url: str
    final_url: str | None
    http_status: int | None
    html: str | None
    method: str = "http"
    redirects: int = 0
    attempts: int = 0
    error: str | None = None


@dataclass(slots=True)
class PageSignals:
    """Everything the parser extracted from one page."""

    kind: PageKind
    matched_signals: list[str] = field(default_factory=list)
    canonical_url: str | None = None
    resolved_asin: str | None = None
    resolved_gti: str | None = None
    page_title: str | None = None     # season/episode title
    show_title: str | None = None     # parentTitle
    title_type: str | None = None     # season | episode | movie
    is_prime: bool | None = None
    territory: str | None = None
    marketplace_id: str | None = None
    offers: list[Offer] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    unavailable_messages: list[str] = field(default_factory=list)
    unrecognized_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CheckResult:
    row: InputRow
    verdict: Verdict
    http_status: int | None
    final_url: str | None
    requested_url: str
    resolved_asin: str | None
    resolved_gti: str | None
    page_title: str | None
    offers: list[Offer]
    matched_signals: list[str]
    checked_at: str
    fetch_method: str
    error: str | None = None
    snapshot_path: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Flat dict for JSONL: input columns first, then check results."""
        record: dict[str, Any] = {
            "content_id": self.row.content_id,
            "title": self.row.title,
            "amazon_url": self.row.amazon_url,
        }
        record.update(self.row.extra)
        record.update(
            {
                "verdict": str(self.verdict),
                "http_status": self.http_status,
                "requested_url": self.requested_url,
                "final_url": self.final_url,
                "resolved_asin": self.resolved_asin,
                "resolved_gti": self.resolved_gti,
                "page_title": self.page_title,
                "offers": [asdict(offer) for offer in self.offers],
                "matched_signals": list(self.matched_signals),
                "checked_at": self.checked_at,
                "fetch_method": self.fetch_method,
                "error": self.error,
                "snapshot_path": self.snapshot_path,
            }
        )
        return record
