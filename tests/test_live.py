"""Smoke test against the real site. Run with: pytest --live

Expected verdicts are what we observed on 2026-11-19 from a UK IP; catalog
changes can legitimately flip them, so a failure here means "look", not "bug".
A BLOCKED result is reported as a skip, not a failure.
"""

from __future__ import annotations

import asyncio

import pytest

from prime_availability_checker.checker import PrimeVideoChecker
from prime_availability_checker.fetcher import FetchSettings
from prime_availability_checker.models import InputRow, Verdict
from prime_availability_checker.rules import load_rules

KNOWN = [
    ("amzn1.dv.gti.6fa0ce00-3f39-4e9e-b1e9-e7fb03c2e43d", "Chef Academy", Verdict.AVAILABLE),
    ("amzn1.dv.gti.bfce7ebe-0f49-400f-b6a1-0aede881d517", "James May: Our Man In Japan", Verdict.AVAILABLE),
    ("amzn1.dv.gti.29adc5a0-23df-4034-b6b3-2826709869f3", "Pushpa Impossible", Verdict.UNAVAILABLE),
    ("amzn1.dv.gti.00000000-0000-0000-0000-000000000000", "", Verdict.UNAVAILABLE),
]


@pytest.mark.live
@pytest.mark.parametrize(("content_id", "title", "expected"), KNOWN)
def test_known_links(content_id: str, title: str, expected: Verdict) -> None:
    rules = load_rules()
    settings = FetchSettings(concurrency=1, delay_min=2.0, delay_max=4.0)

    async def run() -> Verdict:
        async with PrimeVideoChecker(rules, rules.marketplace("uk"), settings) as checker:
            result, _ = await checker.check(InputRow(content_id, title, content_id))
            return result.verdict

    verdict = asyncio.run(run())
    if verdict is Verdict.BLOCKED:
        pytest.skip("Blocked by Amazon; not a content verdict")
    assert verdict is expected
