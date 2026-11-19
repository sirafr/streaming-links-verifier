"""Read alert rows from CSV, JSONL, or a crawler catalog-diff log."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .models import InputRow

REQUIRED_FIELDS = ("content_id", "title", "amazon_url")

# "12) No Show "Title" (amzn1.dv.gti....): https://... in 2026-11-02_....ldjson"
_LOG_ENTRY_RE = re.compile(
    r'^\d+\) No (?P<kind>Show|Episode) "(?P<title>.*)" \((?P<id>[^)\s]+)\): (?P<url>\S+) in (?P<file>\S+)$'
)
# "[Show] <new file> vs <old file>"
_LOG_SECTION_RE = re.compile(r"^\[(?P<kind>Show|Episode)\] (?P<left>\S+) vs (?P<right>\S+)$")
_LOG_SLUG_RE = re.compile(r"^Slug: (?P<slug>\S+)")
_LOG_OLD_RE = re.compile(r"^Old: (?P<file>\S+)")
_LOG_NEW_RE = re.compile(r"^New: (?P<file>\S+)")


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix in (".jsonl", ".ndjson"):
        return "jsonl"
    with path.open(encoding="utf-8") as handle:
        first_line = handle.readline()
    if first_line.startswith("Slug:"):
        return "prime-log"
    raise ValueError(f"Cannot tell the format of {path}; pass --format")


def load_rows(path: Path, fmt: str = "auto", kinds: tuple[str, ...] = ("Show",)) -> list[InputRow]:
    if fmt == "auto":
        fmt = detect_format(path)
    if fmt == "csv":
        return _load_csv(path)
    if fmt == "jsonl":
        return _load_jsonl(path)
    if fmt == "prime-log":
        return load_prime_log(path, kinds=kinds)
    raise ValueError(f"Unknown input format {fmt!r}")


def _row_from_mapping(record: dict[str, Any], line_number: int) -> InputRow:
    missing = [name for name in REQUIRED_FIELDS if not str(record.get(name) or "").strip()]
    if "amazon_url" in missing and str(record.get("content_id") or "").strip():
        # A bare ASIN/GTI in content_id is enough to build the URL.
        missing.remove("amazon_url")
    if "title" in missing:
        missing.remove("title")  # title is only used for the sanity check
    if missing:
        raise ValueError(f"Row {line_number}: missing {', '.join(missing)}")

    extra = {key: value for key, value in record.items() if key not in REQUIRED_FIELDS}
    return InputRow(
        content_id=str(record.get("content_id") or "").strip(),
        title=str(record.get("title") or "").strip(),
        amazon_url=str(record.get("amazon_url") or "").strip(),
        extra=extra,
    )


def _load_csv(path: Path) -> list[InputRow]:
    rows: list[InputRow] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for line_number, record in enumerate(reader, start=2):
            rows.append(_row_from_mapping(record, line_number))
    return rows


def _load_jsonl(path: Path) -> list[InputRow]:
    rows: list[InputRow] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            rows.append(_row_from_mapping(json.loads(line), line_number))
    return rows


def load_prime_log(path: Path, kinds: tuple[str, ...] = ("Show",)) -> list[InputRow]:
    """Read the "no longer offered" entries from a catalog-diff log.

    The log has two sections per kind: "[Show] NEW vs OLD" lists items that are
    in OLD but missing from NEW (the alerts), and "[Show] OLD vs NEW" lists items
    that are new in NEW. We return the first kind as rows and use the second to
    fill `new_content_id` when the same title shows up under a different ID.
    """
    slug = ""
    old_file = ""
    new_file = ""
    section: str | None = None   # "removed" | "added"
    section_kind = ""
    removed: list[dict[str, str]] = []
    added_ids: dict[tuple[str, str], list[str]] = defaultdict(list)

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if match := _LOG_SLUG_RE.match(line):
                slug = match["slug"]
                continue
            if match := _LOG_OLD_RE.match(line):
                old_file = match["file"]
                continue
            if match := _LOG_NEW_RE.match(line):
                new_file = match["file"]
                continue
            if match := _LOG_SECTION_RE.match(line):
                section_kind = match["kind"]
                section = "removed" if match["left"] == new_file else "added"
                continue
            if line.startswith("_____") or line.startswith("====="):
                section = None
                continue

            entry = _LOG_ENTRY_RE.match(line)
            if entry is None or section is None or entry["kind"] not in kinds:
                continue
            if section == "removed":
                removed.append(entry.groupdict())
            else:
                key = (entry["kind"], _title_key(entry["title"]))
                added_ids[key].append(entry["id"])

    rows: list[InputRow] = []
    for entry in removed:
        new_ids = added_ids.get((entry["kind"], _title_key(entry["title"])), [])
        rows.append(
            InputRow(
                content_id=entry["id"],
                title=entry["title"],
                amazon_url=entry["url"],
                extra={
                    "source": slug,
                    "kind": entry["kind"],
                    "old_snapshot": old_file,
                    "new_snapshot": new_file,
                    "new_content_id": ";".join(new_ids),
                },
            )
        )
    return rows


def _title_key(title: str) -> str:
    return " ".join(title.lower().split())
