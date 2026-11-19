"""Classifier tests: one or more fixtures per verdict (no network)."""

from __future__ import annotations

from prime_availability_checker.checker import PrimeVideoChecker
from prime_availability_checker.classifier import title_similarity
from prime_availability_checker.models import FetchResult, InputRow, Verdict

from .conftest import fetched, load_fixture, row


def verdict_of(checker: PrimeVideoChecker, fetch: FetchResult, title: str = "") -> tuple[Verdict, list[str]]:
    result = checker.evaluate(row(title), fetch)
    return result.verdict, result.matched_signals


# --- AVAILABLE ---------------------------------------------------------------

def test_available_channel_and_buy(checker: PrimeVideoChecker) -> None:
    verdict, signals = verdict_of(checker, fetched("available_channel_and_buy.html.gz"), "Chef Academy")
    assert verdict is Verdict.AVAILABLE
    assert "offer:channel" in signals and "offer:buy" in signals


def test_available_prime(checker: PrimeVideoChecker) -> None:
    verdict, _ = verdict_of(checker, fetched("available_prime_only.html.gz"), "James May: Our Man In Japan")
    assert verdict is Verdict.AVAILABLE


def test_available_free_with_ads(checker: PrimeVideoChecker) -> None:
    verdict, signals = verdict_of(checker, fetched("available_free_with_ads.html.gz"), "Ashes to Ashes")
    assert verdict is Verdict.AVAILABLE
    assert "offer:free" in signals


def test_placeholder_page_title_skips_title_check(checker: PrimeVideoChecker) -> None:
    # The HTML <title> says "placeholder_title", but parentTitle is correct.
    verdict, _ = verdict_of(checker, fetched("available_placeholder_title.html.gz"), "Office Blue: Our Christmas")
    assert verdict is Verdict.AVAILABLE


# --- UNAVAILABLE -------------------------------------------------------------

def test_unavailable_message(checker: PrimeVideoChecker) -> None:
    verdict, signals = verdict_of(checker, fetched("unavailable_currently.html.gz"), "Ufff..Yeh Love Hai Mushkil")
    assert verdict is Verdict.UNAVAILABLE
    assert "unavailable_message:currently unavailable" in signals


def test_unavailable_no_longer_in_region(checker: PrimeVideoChecker) -> None:
    verdict, signals = verdict_of(
        checker, fetched("unavailable_no_longer_in_region.html.gz"), "Dan & Phil Interactive Introverts"
    )
    assert verdict is Verdict.UNAVAILABLE
    assert "unavailable_message:no longer available" in signals


def test_trailer_only_page_is_unavailable(checker: PrimeVideoChecker) -> None:
    # The only button is "Watch trailer"; a trailer is not an offer.
    verdict, signals = verdict_of(checker, fetched("unavailable_trailer_only.html.gz"), "The Boy Who Stole Billions?")
    assert verdict is Verdict.UNAVAILABLE
    assert "no_offers_on_detail_page" in signals


def test_unavailable_404(checker: PrimeVideoChecker) -> None:
    verdict, signals = verdict_of(checker, fetched("unavailable_not_found_404.html.gz", status=404))
    assert verdict is Verdict.UNAVAILABLE
    assert "http_404" in signals


def test_unavailable_when_buy_box_is_empty(checker: PrimeVideoChecker) -> None:
    html = load_fixture("available_prime_only.html.gz")
    # Drop every buy-box action, keeping the rest of a valid detail page.
    html = html.replace('"primaryActions":[', '"primaryActionsRemoved":[')
    fetch = FetchResult("https://www.amazon.co.uk/gp/video/detail/x", None, 200, html)
    verdict, signals = verdict_of(checker, fetch)
    assert verdict is Verdict.UNAVAILABLE
    assert "no_offers_on_detail_page" in signals


# --- BLOCKED (never UNAVAILABLE) ----------------------------------------------

def test_blocked_captcha(checker: PrimeVideoChecker) -> None:
    verdict, _ = verdict_of(checker, fetched("blocked_captcha_SYNTHETIC.html.gz"))
    assert verdict is Verdict.BLOCKED


def test_blocked_captcha_served_with_404_is_still_blocked(checker: PrimeVideoChecker) -> None:
    verdict, _ = verdict_of(checker, fetched("blocked_captcha_SYNTHETIC.html.gz", status=404))
    assert verdict is Verdict.BLOCKED


def test_blocked_on_503_and_fetch_errors(checker: PrimeVideoChecker) -> None:
    throttled = FetchResult("https://x", "https://x", 503, "<html>Service Unavailable</html>")
    failed = FetchResult("https://x", None, None, None, error="ReadTimeout")
    assert verdict_of(checker, throttled)[0] is Verdict.BLOCKED
    assert verdict_of(checker, failed)[0] is Verdict.BLOCKED


# --- REDIRECTED --------------------------------------------------------------

def test_redirected_to_other_title(checker: PrimeVideoChecker) -> None:
    fetch = fetched(
        "available_channel_and_buy.html.gz",
        url="https://www.amazon.co.uk/gp/video/detail/amzn1.dv.gti.00000000-0000-0000-0000-000000000001",
        final_url="https://www.amazon.co.uk/gp/video/detail/B0HR3W4ZJG",
        redirects=1,
    )
    verdict, signals = verdict_of(checker, fetch, "Chef Academy")
    assert verdict is Verdict.REDIRECTED
    assert "redirected_to:B0HR3W4ZJG" in signals


def test_show_link_served_as_season_page_is_not_a_redirect(checker: PrimeVideoChecker) -> None:
    # Normal case: show GTI URL, HTTP 200, canonical is the season ASIN.
    verdict, _ = verdict_of(checker, fetched("available_channel_and_buy.html.gz"), "Chef Academy")
    assert verdict is Verdict.AVAILABLE


# --- UNKNOWN -----------------------------------------------------------------

def test_unknown_wrong_territory(checker: PrimeVideoChecker) -> None:
    html = load_fixture("available_prime_only.html.gz").replace('"currentTerritory":"GB"', '"currentTerritory":"US"')
    fetch = FetchResult("https://www.amazon.co.uk/gp/video/detail/x", None, 200, html)
    verdict, signals = verdict_of(checker, fetch)
    assert verdict is Verdict.UNKNOWN
    assert "territory_mismatch:US!=GB" in signals


def test_unknown_title_mismatch(checker: PrimeVideoChecker) -> None:
    verdict, signals = verdict_of(checker, fetched("available_channel_and_buy.html.gz"), "Peaky Blinders")
    assert verdict is Verdict.UNKNOWN
    assert any(signal.startswith("title_mismatch:") for signal in signals)


def test_unknown_unrecognized_page(checker: PrimeVideoChecker) -> None:
    fetch = FetchResult("https://x", "https://x", 200, "<html><body>New layout</body></html>")
    verdict, signals = verdict_of(checker, fetch)
    assert verdict is Verdict.UNKNOWN
    assert signals == ["no_hydration_json"]


def test_unknown_contradictory(checker: PrimeVideoChecker) -> None:
    html = load_fixture("available_prime_only.html.gz").replace(
        '"string":"Auto-renews at', '"string":"This title is currently unavailable. Auto-renews at', 1
    )
    fetch = FetchResult("https://www.amazon.co.uk/gp/video/detail/x", None, 200, html)
    verdict, signals = verdict_of(checker, fetch)
    assert verdict is Verdict.UNKNOWN
    assert "contradictory:offers_and_unavailable_message" in signals


# --- helpers -------------------------------------------------------------------

def test_title_similarity_handles_season_suffixes() -> None:
    assert title_similarity("Xena: Warrior Princess", "Xena: Warrior Princess - Season 2") == 1.0
    assert title_similarity("Pushpa Impossible", "Pushpa Impossible: 1-100") == 1.0
    assert title_similarity("Chef Academy", "Peaky Blinders") < 0.5


def test_build_url_normalizes_links(checker: PrimeVideoChecker) -> None:
    gti = "amzn1.dv.gti.6fa0ce00-3f39-4e9e-b1e9-e7fb03c2e43d"
    expected = f"https://www.amazon.co.uk/gp/video/detail/{gti}"
    for link in (
        f"https://www.amazon.com/gp/video/detail/{gti}?ref_=x",
        f"https://www.primevideo.com/detail/{gti}/",
        gti,
    ):
        assert checker.build_url(InputRow("id", "", link)) == expected

    asin_link = InputRow("id", "", "https://www.amazon.co.uk/dp/B0HR3W4ZJG")
    assert checker.build_url(asin_link) == "https://www.amazon.co.uk/gp/video/detail/B0HR3W4ZJG"
