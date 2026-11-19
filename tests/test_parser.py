"""Parser tests against saved Prime Video UK pages (no network)."""

from __future__ import annotations

from prime_availability_checker.models import FetchResult, PageKind
from prime_availability_checker.parser import clean_label, parse_page
from prime_availability_checker.rules import Rules

from .conftest import fetched


def offer_shorts(page) -> list[str]:
    return [offer.short() for offer in page.offers]


def test_channel_and_buy_offers(rules: Rules) -> None:
    page = parse_page(fetched("available_channel_and_buy.html.gz"), rules)

    assert page.kind is PageKind.DETAIL
    assert page.resolved_asin == "B0HR3W4ZJG"
    assert page.resolved_gti == "amzn1.dv.gti.d4a9f768-adda-b485-ea43-cef9666b5d55"
    assert page.show_title == "Chef Academy"
    assert page.territory == "GB"
    assert offer_shorts(page) == [
        "channel:Hayu",
        "buy:£2.49 (EPISODE SD)",
        "buy:£7.99 (SEASON SD)",
    ]
    assert page.unavailable_messages == []


def test_prime_included(rules: Rules) -> None:
    page = parse_page(fetched("available_prime_only.html.gz"), rules)

    assert offer_shorts(page) == ["prime:included"]
    assert "offer:prime" in page.matched_signals


def test_season_pass_scope(rules: Rules) -> None:
    page = parse_page(fetched("available_buy_season_pass.html.gz"), rules)

    assert "buy:£7.99 (SEASON_PASS SD)" in offer_shorts(page)


def test_free_with_ads_play_button(rules: Rules) -> None:
    page = parse_page(fetched("available_free_with_ads.html.gz"), rules)

    types = [offer.type for offer in page.offers]
    assert "free" in types
    assert "prime" in types
    assert "play" not in types


def test_trailers_are_not_offers(rules: Rules) -> None:
    page = parse_page(fetched("unavailable_trailer_only.html.gz"), rules)

    assert page.offers == []
    assert page.unrecognized_actions == []


def test_episode_level_fallback(rules: Rules) -> None:
    # Season box: "Watch now" (first episode free, FVOD). Episodes: MGM+ / buy.
    page = parse_page(fetched("available_episode_level_fallback.html.gz"), rules)

    types = [offer.type for offer in page.offers]
    assert types[0] == "free"
    assert "channel" in types
    assert "episode_level_offers" in page.matched_signals
    assert all(offer.scope for offer in page.offers[1:])


def test_episode_fallback_skipped_when_season_has_paid_offer(rules: Rules) -> None:
    page = parse_page(fetched("available_channel_and_buy.html.gz"), rules)

    assert "episode_level_offers" not in page.matched_signals


def test_currently_unavailable_message(rules: Rules) -> None:
    page = parse_page(fetched("unavailable_currently.html.gz"), rules)

    assert page.kind is PageKind.DETAIL
    assert page.offers == []
    assert page.unavailable_messages == ["currently unavailable"]
    assert page.resolved_asin == "B0HFXZ78N6"


def test_page_not_found(rules: Rules) -> None:
    page = parse_page(fetched("unavailable_not_found_404.html.gz", status=404), rules)

    assert page.kind is PageKind.NOT_FOUND
    assert "http_404" in page.matched_signals
    assert any(signal.startswith("not_found_page:") for signal in page.matched_signals)


def test_captcha_is_blocked_even_with_200(rules: Rules) -> None:
    page = parse_page(fetched("blocked_captcha_SYNTHETIC.html.gz", status=200), rules)

    assert page.kind is PageKind.BLOCKED
    assert page.matched_signals[0].startswith("captcha:")


def test_404_footer_is_not_mistaken_for_captcha(rules: Rules) -> None:
    # The "Dogs of Amazon" 404 page talks about "automated access" in its footer.
    html = "<title>Page Not Found</title><p>To discuss automated access to Amazon data please contact api-services-support@amazon.com</p>"
    fetch = FetchResult("https://x/gp/video/detail/B000000000", None, 404, html)

    assert parse_page(fetch, rules).kind is PageKind.NOT_FOUND


def test_throttle_statuses_are_blocked(rules: Rules) -> None:
    for status in (403, 429, 503, 500):
        fetch = FetchResult("https://x", "https://x", status, "<html></html>")
        assert parse_page(fetch, rules).kind is PageKind.BLOCKED, status


def test_fetch_failure(rules: Rules) -> None:
    fetch = FetchResult("https://x", None, None, None, error="ConnectTimeout: timed out")
    page = parse_page(fetch, rules)

    assert page.kind is PageKind.FETCH_FAILED
    assert page.matched_signals == ["fetch_failed:ConnectTimeout: timed out"]


def test_page_without_hydration_json(rules: Rules) -> None:
    fetch = FetchResult("https://x", "https://x", 200, "<html><body>New layout</body></html>")

    assert parse_page(fetch, rules).kind is PageKind.UNRECOGNIZED


def test_clean_label() -> None:
    label = "Buy Season 1 {conditionalLineBreak}{bold}SD{end} £7.99"
    assert clean_label(label) == "Buy Season 1 SD £7.99"
