"""Load rules.yaml and precompile its patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_RULES_PATH = Path(__file__).with_name("rules.yaml")


@dataclass(frozen=True, slots=True)
class Marketplace:
    name: str
    host: str
    accept_language: str
    territory: str


def _compile_all(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled = []
    for pattern in patterns:
        compiled.append(re.compile(pattern, re.IGNORECASE))
    return compiled


class Rules:
    """Typed accessors over the raw YAML, with regexes compiled once."""

    def __init__(self, raw: dict[str, Any], source: Path | None = None) -> None:
        self.raw = raw
        self.source = source

        self.blocked_statuses: set[int] = set(raw["blocked"]["http_status"])
        self.blocked_patterns = _compile_all(raw["blocked"]["text_patterns"])
        self.not_found_statuses: set[int] = set(raw["not_found"]["http_status"])
        self.not_found_patterns = _compile_all(raw["not_found"]["text_patterns"])

        self.hydration: dict[str, Any] = raw["hydration"]

        actions = raw["actions"]
        self.container_types: set[str] = set(actions["container_types"])
        self.ignore_types: set[str] = set(actions["ignore_types"])
        self.message_types: set[str] = set(actions["message_types"])
        self.subscribe_types: set[str] = set(actions["subscribe_types"])
        self.transact_types: set[str] = set(actions["transact_types"])
        self.play_types: set[str] = set(actions["play_types"])
        self.subscription_types: dict[str, str] = dict(actions["subscription_types"])
        self.transaction_types: dict[str, str] = dict(actions["transaction_types"])
        self.free_benefit_ids: set[str] = {
            value.lower() for value in actions.get("free_benefit_ids", [])
        }
        self.ignore_material_types: set[str] = {
            value.lower() for value in actions.get("ignore_material_types", [])
        }
        self.ignore_label_patterns = _compile_all(actions.get("ignore_label_patterns", []))
        self.free_label_patterns = _compile_all(actions["free_label_patterns"])
        self.episode_actions_path: list[str] | None = raw["hydration"].get("episode_actions_path")
        self.channel_name_re = re.compile(actions["channel_name_pattern"], re.IGNORECASE)
        self.price_re = re.compile(actions["price_pattern"])
        self.quality_re = re.compile(actions["quality_pattern"], re.IGNORECASE)

        self.unavailable_patterns = _compile_all(raw["unavailable_messages"])

        self.require_territory_match: bool = bool(raw.get("require_territory_match", True))
        title_check = raw.get("title_check", {})
        self.title_check_enabled: bool = bool(title_check.get("enabled", False))
        self.title_min_similarity: float = float(title_check.get("min_similarity", 0.5))
        self.placeholder_titles: set[str] = {
            title.lower() for title in title_check.get("placeholder_titles", [])
        }

        self.default_marketplace: str = raw.get("default_marketplace", "uk")
        self._marketplaces: dict[str, dict[str, str]] = raw["marketplaces"]

    def marketplace(self, name: str | None = None) -> Marketplace:
        key = (name or self.default_marketplace).lower()
        if key not in self._marketplaces:
            known = ", ".join(sorted(self._marketplaces))
            raise ValueError(f"Unknown marketplace {key!r}; known: {known}")
        entry = self._marketplaces[key]
        return Marketplace(
            name=key,
            host=entry["host"],
            accept_language=entry["accept_language"],
            territory=entry["territory"],
        )


def load_rules(path: str | Path | None = None) -> Rules:
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    with rules_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return Rules(raw, source=rules_path)
