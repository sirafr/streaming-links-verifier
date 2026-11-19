"""Turn a fetched page into PageSignals: page kind, IDs, offers, messages.

The parser only extracts facts. Deciding what they mean is the classifier's
job, so the two can be tested separately.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any

from .models import FetchResult, Offer, PageKind, PageSignals
from .rules import Rules

_CANONICAL_RE = re.compile(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', re.IGNORECASE)
_ASIN_IN_URL_RE = re.compile(r"/(?:detail|dp)/([A-Z0-9]{10})(?:[/?]|$)")
_TEMPLATE_TOKEN_RE = re.compile(r"\{[a-zA-Z|]+\}")
_SPACES_RE = re.compile(r"\s+")


def first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    """Return the source of the first pattern found in text, if any."""
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return None


def extract_script_json(page_html: str, script_id: str) -> Any | None:
    """Parse the JSON body of <script id="script_id">, or None."""
    pattern = re.compile(
        r'<script[^>]*\bid="' + re.escape(script_id) + r'"[^>]*>(.*?)</script>',
        re.DOTALL,
    )
    match = pattern.search(page_html)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def dig(data: Any, path: list[str]) -> Any | None:
    """Follow a list of keys into nested dicts; None if any key is missing."""
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def clean_label(label: str | None) -> str:
    """'Buy Season 1 {conditionalLineBreak}{bold}SD{end} £7.99' -> 'Buy Season 1 SD £7.99'."""
    if not label:
        return ""
    text = _TEMPLATE_TOKEN_RE.sub(" ", label)
    return _SPACES_RE.sub(" ", text).strip()


def parse_page(fetch: FetchResult, rules: Rules) -> PageSignals:
    if fetch.html is None:
        reason = fetch.error or "no response body"
        return PageSignals(kind=PageKind.FETCH_FAILED, matched_signals=[f"fetch_failed:{reason}"])

    page_html = fetch.html
    status = fetch.http_status

    captcha = first_match(rules.blocked_patterns, page_html)
    if captcha is not None:
        return PageSignals(kind=PageKind.BLOCKED, matched_signals=[f"captcha:{captcha}"])

    if status in rules.blocked_statuses:
        return PageSignals(kind=PageKind.BLOCKED, matched_signals=[f"http_{status}"])

    if status in rules.not_found_statuses:
        signals = [f"http_{status}"]
        page_marker = first_match(rules.not_found_patterns, page_html)
        if page_marker is not None:
            signals.append(f"not_found_page:{page_marker}")
        return PageSignals(kind=PageKind.NOT_FOUND, matched_signals=signals)

    if status is not None and status >= 500:
        return PageSignals(kind=PageKind.BLOCKED, matched_signals=[f"http_{status}"])

    if status != 200:
        return PageSignals(kind=PageKind.UNRECOGNIZED, matched_signals=[f"http_{status}"])

    hydration = extract_script_json(page_html, rules.hydration["page_script_id"])
    if hydration is None:
        soft_404 = first_match(rules.not_found_patterns, page_html)
        if soft_404 is not None:
            return PageSignals(kind=PageKind.NOT_FOUND, matched_signals=[f"soft_404:{soft_404}"])
        return PageSignals(kind=PageKind.UNRECOGNIZED, matched_signals=["no_hydration_json"])

    return _parse_detail(page_html, hydration, rules)


def _parse_detail(page_html: str, hydration: Any, rules: Rules) -> PageSignals:
    signals = PageSignals(kind=PageKind.DETAIL, matched_signals=["hydration_json"])
    paths = rules.hydration

    canonical = _CANONICAL_RE.search(page_html)
    if canonical is not None:
        signals.canonical_url = html_lib.unescape(canonical.group(1))

    head_meta = dig(hydration, paths["head_metadata_path"]) or {}
    asin = head_meta.get("pageTypeId")
    if not asin and signals.canonical_url:
        asin_match = _ASIN_IN_URL_RE.search(signals.canonical_url)
        if asin_match is not None:
            asin = asin_match.group(1)
    signals.resolved_asin = asin

    header_details = dig(hydration, paths["header_detail_path"]) or {}
    header = header_details.get(asin) if asin else None
    if header is None and len(header_details) == 1:
        header = next(iter(header_details.values()))
    if isinstance(header, dict):
        signals.page_title = header.get("title")
        signals.show_title = header.get("parentTitle") or header.get("title")
        signals.title_type = header.get("titleType")
        signals.resolved_gti = header.get("catalogId")
        signals.is_prime = header.get("isPrime")

    global_store = extract_script_json(page_html, paths["global_script_id"])
    if global_store is not None:
        signals.territory = dig(global_store, paths["territory_path"])
        signals.marketplace_id = dig(global_store, paths["marketplace_id_path"])
    signals.matched_signals.append(f"territory:{signals.territory}")

    buy_boxes = dig(hydration, paths["actions_path"]) or {}
    if asin and asin in buy_boxes:
        selected = [buy_boxes[asin]]
    else:
        selected = list(buy_boxes.values())
    if not selected:
        signals.matched_signals.append("no_buybox")

    for box in selected:
        _read_buy_box(box, rules, signals)

    has_strong_offer = any(offer.type not in ("free", "play") for offer in signals.offers)
    if not has_strong_offer and rules.episode_actions_path:
        _read_episode_boxes(hydration, rules, signals)

    _dedupe_offers(signals)
    for offer in signals.offers:
        tag = f"offer:{offer.type}"
        if tag not in signals.matched_signals:
            signals.matched_signals.append(tag)
    for message in signals.unavailable_messages:
        signals.matched_signals.append(f"unavailable_message:{message}")
    for action_type in signals.unrecognized_actions:
        signals.matched_signals.append(f"unrecognized_action:{action_type}")
    return signals


def _read_buy_box(box: dict[str, Any], rules: Rules, signals: PageSignals) -> None:
    messages = box.get("messages") or {}
    focus = dig(messages, ["focusMessage", "dvMessage", "string"])
    if focus:
        _record_message(clean_label(focus), rules, signals)

    _walk_actions(box.get("primaryActions") or [], rules, signals)
    _walk_actions(box.get("secondaryActions") or [], rules, signals)


def _read_episode_boxes(hydration: Any, rules: Rules, signals: PageSignals) -> None:
    """Collect offers from the episode list's buy boxes (offers only, no messages).

    Episode messages are not recorded: a single unavailable episode must not
    turn a season "unavailable". Offers found here are tagged scope EPISODE.
    """
    assert rules.episode_actions_path is not None
    episode_boxes = dig(hydration, rules.episode_actions_path) or {}
    scratch = PageSignals(kind=PageKind.DETAIL)
    for box in episode_boxes.values():
        if isinstance(box, dict):
            _walk_actions(box.get("primaryActions") or [], rules, scratch)

    known_types = {offer.type for offer in signals.offers}
    added = 0
    for offer in scratch.offers:
        if offer.type in known_types:
            continue
        offer.scope = offer.scope or "EPISODE"
        signals.offers.append(offer)
        added += 1
    if added:
        signals.matched_signals.append("episode_level_offers")


def _record_message(text: str, rules: Rules, signals: PageSignals) -> None:
    signals.messages.append(text)
    for pattern in rules.unavailable_patterns:
        match = pattern.search(text)
        if match is not None:
            signals.unavailable_messages.append(match.group(0).lower())
            return


def _walk_actions(actions: list[dict[str, Any]], rules: Rules, signals: PageSignals) -> None:
    for action in actions:
        action_type = action.get("actionType") or ""
        metadata = _action_metadata(action)
        label = metadata.get("label") or dig(action, ["payload", "presentation", "primaryLabel"])

        if action_type in rules.container_types:
            nested = dig(action, ["payload", "expandingCard", "actions"])
            if nested:
                _walk_actions(nested, rules, signals)
                continue
            # A container with nothing inside: read its own metadata instead.
            action_type = metadata.get("actionType") or action_type

        if action_type in rules.ignore_types:
            continue

        material = str(metadata.get("videoMaterialType") or "").lower()
        if material in rules.ignore_material_types:
            continue
        if first_match(rules.ignore_label_patterns, clean_label(label)) is not None:
            continue

        if action_type in rules.message_types:
            _record_message(clean_label(label), rules, signals)
            continue

        offer = _interpret_offer(action_type, metadata, label, rules)
        if offer is None:
            signals.unrecognized_actions.append(action_type or "<missing>")
        else:
            signals.offers.append(offer)


def _action_metadata(action: dict[str, Any]) -> dict[str, Any]:
    raw = action.get("actionMetadata")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _interpret_offer(
    action_type: str,
    metadata: dict[str, Any],
    label: str | None,
    rules: Rules,
) -> Offer | None:
    text = clean_label(label)
    benefit = str(metadata.get("benefitId") or "").lower()

    if benefit in rules.free_benefit_ids:
        return Offer(type="free", price="free", quality=_quality(metadata, label, rules), label=text)

    if first_match(rules.free_label_patterns, text) is not None:
        return Offer(type="free", price="free", label=text)

    if action_type in rules.subscribe_types:
        offer_type = rules.subscription_types.get(metadata.get("subscriptionType") or "")
        if offer_type is None:
            return None
        if offer_type == "prime":
            return Offer(type="prime", price="included", label=text)
        if offer_type == "free":
            return Offer(type="free", price="free", label=text)
        provider = _channel_name(label, rules) or metadata.get("benefitId")
        return Offer(type=offer_type, provider=provider, label=text)

    if action_type in rules.transact_types:
        offer_type = rules.transaction_types.get(metadata.get("transactionType") or "")
        if offer_type is None:
            return None
        return Offer(
            type=offer_type,
            price=_price(metadata, text, rules),
            scope=metadata.get("offerFamily"),
            quality=_quality(metadata, label, rules),
            label=text,
        )

    if action_type in rules.play_types:
        return Offer(type="play", label=text)

    return None


def _channel_name(label: str | None, rules: Rules) -> str | None:
    if not label:
        return None
    match = rules.channel_name_re.search(label)
    if match is None:
        return None
    return clean_label(match.group(1)) or None


def _price(metadata: dict[str, Any], text: str, rules: Rules) -> str | None:
    if metadata.get("price") is not None:
        currency = metadata.get("currency") or ""
        return f"{currency}{metadata['price']}".strip()
    match = rules.price_re.search(text)
    if match is None:
        return None
    return f"{match.group(1)}{match.group(2)}"


def _quality(metadata: dict[str, Any], label: str | None, rules: Rules) -> str | None:
    if metadata.get("videoQuality"):
        return str(metadata["videoQuality"])
    if not label:
        return None
    match = rules.quality_re.search(label)
    return match.group(1).upper() if match else None


def _dedupe_offers(signals: PageSignals) -> None:
    seen: set[tuple[str | None, ...]] = set()
    unique: list[Offer] = []
    for offer in signals.offers:
        if offer.key() in seen:
            continue
        seen.add(offer.key())
        unique.append(offer)
    signals.offers = unique
