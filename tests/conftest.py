from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from prime_availability_checker.checker import PrimeVideoChecker
from prime_availability_checker.models import FetchResult, InputRow
from prime_availability_checker.rules import Rules, load_rules

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="Run tests that hit amazon.co.uk")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="needs --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def load_fixture(name: str) -> str:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as handle:
        return handle.read()


def fetched(
    name: str,
    status: int = 200,
    url: str = "https://www.amazon.co.uk/gp/video/detail/amzn1.dv.gti.00000000-0000-0000-0000-000000000001",
    final_url: str | None = None,
    redirects: int = 0,
) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=final_url or url,
        http_status=status,
        html=load_fixture(name),
        redirects=redirects,
    )


@pytest.fixture(scope="session")
def rules() -> Rules:
    return load_rules()


@pytest.fixture(scope="session")
def checker(rules: Rules) -> PrimeVideoChecker:
    # parse/classify don't need the network client, so no `async with` here.
    return PrimeVideoChecker(rules, rules.marketplace("uk"))


def row(title: str = "", content_id: str = "amzn1.dv.gti.00000000-0000-0000-0000-000000000001") -> InputRow:
    return InputRow(content_id=content_id, title=title, amazon_url="")
