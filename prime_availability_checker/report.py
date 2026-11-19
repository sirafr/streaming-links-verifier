"""CSV/JSONL report writing and the end-of-run summary."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, TextIO

from .models import CheckResult, Verdict

CORE_COLUMNS = ["content_id", "title", "amazon_url"]
RESULT_COLUMNS = [
    "verdict",
    "http_status",
    "requested_url",
    "final_url",
    "resolved_asin",
    "resolved_gti",
    "page_title",
    "offers",
    "matched_signals",
    "checked_at",
    "fetch_method",
    "error",
    "snapshot_path",
]


def report_paths(output: Path) -> tuple[Path, Path, Path]:
    """report.csv -> (report.csv, report.jsonl, report.summary.txt).

    Only a trailing .csv/.jsonl is stripped, so dotted names such as
    "report.reclassified.csv" keep their full stem (Path.with_suffix would
    turn that into "report.csv" and clobber the original run).
    """
    name = output.name
    for extension in (".csv", ".jsonl"):
        if name.lower().endswith(extension):
            name = name[: -len(extension)]
            break
    parent = output.parent
    return parent / f"{name}.csv", parent / f"{name}.jsonl", parent / f"{name}.summary.txt"


class JsonlAppender:
    """Writes one JSON record per finished row, flushed immediately."""

    def __init__(self, path: Path, append: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO = path.open("a" if append else "w", encoding="utf-8")

    def write(self, result: CheckResult) -> None:
        self._handle.write(json.dumps(result.to_record(), ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def latest_per_content(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the last record per content_id (a resumed/retried run appends)."""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        latest[record["content_id"]] = record
    return list(latest.values())


def format_offers(offers: list[dict[str, Any]]) -> str:
    parts = []
    for offer in offers:
        text = offer["type"]
        if offer.get("provider"):
            text += f":{offer['provider']}"
        if offer.get("price"):
            text += f":{offer['price']}"
        qualifiers = [offer[key] for key in ("scope", "quality") if offer.get(key)]
        if qualifiers:
            text += f" ({' '.join(qualifiers)})"
        parts.append(text)
    return "; ".join(parts)


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    extra_columns: list[str] = []
    for record in records:
        for key in record:
            if key not in CORE_COLUMNS and key not in RESULT_COLUMNS and key not in extra_columns:
                extra_columns.append(key)

    columns = CORE_COLUMNS + extra_columns + RESULT_COLUMNS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["offers"] = format_offers(record.get("offers") or [])
            row["matched_signals"] = "; ".join(record.get("matched_signals") or [])
            writer.writerow(row)


def build_summary(records: list[dict[str, Any]]) -> str:
    counts = Counter(record["verdict"] for record in records)
    total = len(records)
    lines = [f"Checked {total} alert(s)", ""]
    for verdict in Verdict:
        count = counts.get(str(verdict), 0)
        share = f"{100 * count / total:5.1f}%" if total else "  -  "
        lines.append(f"  {verdict:<12} {count:>6}  {share}")

    available = counts.get(str(Verdict.AVAILABLE), 0)
    confirmed = counts.get(str(Verdict.UNAVAILABLE), 0)
    lines.append("")
    lines.append(f"False positives (alert fired, content still AVAILABLE): {available}")
    lines.append(f"True positives (content confirmed UNAVAILABLE):          {confirmed}")
    unchecked = total - available - confirmed
    if unchecked:
        lines.append(f"Not decided (REDIRECTED / BLOCKED / UNKNOWN):            {unchecked}")

    with_new_id = [record for record in records if record.get("new_content_id")]
    if with_new_id:
        churn = Counter(record["verdict"] for record in with_new_id)
        lines.append("")
        lines.append(
            f"Alerts whose title reappears under a new ID in the newer snapshot: {len(with_new_id)}"
        )
        for verdict, count in churn.most_common():
            lines.append(f"  {verdict:<12} {count:>6}")

    offer_types: Counter[str] = Counter()
    for record in records:
        if record["verdict"] != str(Verdict.AVAILABLE):
            continue
        for offer_type in sorted({offer["type"] for offer in record.get("offers") or []}):
            offer_types[offer_type] += 1
    if offer_types:
        lines.append("")
        lines.append("AVAILABLE rows by offer type (a row can have several):")
        for offer_type, count in offer_types.most_common():
            lines.append(f"  {offer_type:<12} {count:>6}")

    return "\n".join(lines) + "\n"


def finalize(jsonl_path: Path, csv_path: Path, summary_path: Path) -> str:
    records = latest_per_content(read_jsonl(jsonl_path))
    write_csv(records, csv_path)
    summary = build_summary(records)
    summary_path.write_text(summary, encoding="utf-8")
    return summary
