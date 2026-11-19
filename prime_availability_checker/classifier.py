"""Map PageSignals to a Verdict.

Rules, in order (first one that applies wins):

1. Fetch failed, captcha, 403/429/5xx            -> BLOCKED  (never a content verdict)
2. 404 / Page Not Found                           -> UNAVAILABLE
3. Page loaded but isn't a detail page we can read -> UNKNOWN
4. Served for the wrong territory                  -> UNKNOWN
5. HTTP redirect to a different title ID           -> REDIRECTED
6. Page's show title doesn't match the alert's     -> UNKNOWN (manual review)
7. Offers and an "unavailable" message together    -> UNKNOWN (contradictory)
8. Offers present                                  -> AVAILABLE
9. "Currently unavailable" style message           -> UNAVAILABLE
10. Buy-box actions we don't recognize             -> UNKNOWN
11. Valid detail page with no offers at all        -> UNAVAILABLE
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from .models import FetchResult, InputRow, PageKind, PageSignals, Verdict
from .rules import Marketplace, Rules

_DETAIL_ID_RE = re.compile(r"/(?:gp/video/detail|detail|dp)/([^/?#]+)")
# Season / volume suffixes Amazon adds to page titles ("Season 2", ": 1-100", "| S1").
_TITLE_SUFFIX_RE = re.compile(
    r"(\s*[-:|]?\s*(season|series|staffel|temporada)\s*\d+.*$)|(\s*[:|]\s*(s\d+|\d+\s*-\s*\d+)\s*$)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Classification:
    verdict: Verdict
    signals: list[str] = field(default_factory=list)
    error: str | None = None


def extract_detail_id(url: str | None) -> str | None:
    if not url:
        return None
    match = _DETAIL_ID_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).lower()
    text = _TITLE_SUFFIX_RE.sub("", text)
    text = re.sub(r"[^\w]+", " ", text)
    return " ".join(text.split())


def title_similarity(alert_title: str, page_title: str) -> float:
    left = normalize_title(alert_title)
    right = normalize_title(page_title)
    if not left or not right:
        return 0.0
    if left == right or left in right or right in left:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def classify(
    row: InputRow,
    fetch: FetchResult,
    page: PageSignals,
    rules: Rules,
    marketplace: Marketplace,
) -> Classification:
    signals = list(page.matched_signals)

    if page.kind in (PageKind.BLOCKED, PageKind.FETCH_FAILED):
        return Classification(Verdict.BLOCKED, signals, error=fetch.error)

    if page.kind is PageKind.NOT_FOUND:
        return Classification(Verdict.UNAVAILABLE, signals)

    if page.kind is not PageKind.DETAIL:
        return Classification(Verdict.UNKNOWN, signals)

    if rules.require_territory_match and page.territory != marketplace.territory:
        signals.append(f"territory_mismatch:{page.territory}!={marketplace.territory}")
        return Classification(Verdict.UNKNOWN, signals)

    requested_id = extract_detail_id(fetch.requested_url)
    final_id = extract_detail_id(fetch.final_url)
    if fetch.redirects > 0 and requested_id and final_id and final_id != requested_id:
        signals.append(f"redirected_to:{final_id}")
        return Classification(Verdict.REDIRECTED, signals)

    if rules.title_check_enabled and row.title and page.show_title:
        if page.show_title.lower() in rules.placeholder_titles:
            signals.append("title_check_skipped:placeholder")
        else:
            similarity = title_similarity(row.title, page.show_title)
            if similarity < rules.title_min_similarity:
                signals.append(f"title_mismatch:{page.show_title!r}({similarity:.2f})")
                return Classification(Verdict.UNKNOWN, signals)

    has_offers = bool(page.offers)
    has_unavailable_message = bool(page.unavailable_messages)

    if has_offers and has_unavailable_message:
        signals.append("contradictory:offers_and_unavailable_message")
        return Classification(Verdict.UNKNOWN, signals)

    if has_offers:
        return Classification(Verdict.AVAILABLE, signals)

    if has_unavailable_message:
        return Classification(Verdict.UNAVAILABLE, signals)

    if page.unrecognized_actions:
        return Classification(Verdict.UNKNOWN, signals)

    signals.append("no_offers_on_detail_page")
    return Classification(Verdict.UNAVAILABLE, signals)
