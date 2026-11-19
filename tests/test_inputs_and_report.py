from __future__ import annotations

import json
from pathlib import Path

from prime_availability_checker.inputs import load_rows
from prime_availability_checker.cli import main
from prime_availability_checker.report import build_summary, report_paths, write_csv

LOG = """Slug: prime
Old: 2026-11-02_1.ldjson - 3 Shows, 0 Episodes
New: 2026-11-18_2.ldjson - 3 Shows, 0 Episodes
______________
===== Shows: old 3 unique, new 3 unique =====
[Show] 2026-11-18_2.ldjson vs 2026-11-02_1.ldjson
0) No Show "Legacies" (amzn1.dv.gti.aaaaaaaa-0000-0000-0000-000000000001): https://www.amazon.co.uk/gp/video/detail/amzn1.dv.gti.aaaaaaaa-0000-0000-0000-000000000001 in 2026-11-02_1.ldjson
1) No Show "Gone Show" (amzn1.dv.gti.aaaaaaaa-0000-0000-0000-000000000002): https://www.amazon.co.uk/gp/video/detail/amzn1.dv.gti.aaaaaaaa-0000-0000-0000-000000000002 in 2026-11-02_1.ldjson
Total: 2
______________
[Show] 2026-11-02_1.ldjson vs 2026-11-18_2.ldjson
0) No Show "Legacies" (amzn1.dv.gti.bbbbbbbb-0000-0000-0000-000000000001): https://www.amazon.co.uk/gp/video/detail/amzn1.dv.gti.bbbbbbbb-0000-0000-0000-000000000001 in 2026-11-18_2.ldjson
______________
===== Episodes: old 1 unique, new 0 unique =====
[Episode] 2026-11-18_2.ldjson vs 2026-11-02_1.ldjson
0) No Episode "Pilot" (amzn1.dv.gti.cccccccc-0000-0000-0000-000000000001): https://www.amazon.co.uk/gp/video/detail/amzn1.dv.gti.cccccccc-0000-0000-0000-000000000001 in 2026-11-02_1.ldjson
______________
"""


def test_prime_log_reads_removed_shows_and_new_ids(tmp_path: Path) -> None:
    log_path = tmp_path / "prime.log"
    log_path.write_text(LOG, encoding="utf-8")

    rows = load_rows(log_path)

    assert [row.title for row in rows] == ["Legacies", "Gone Show"]
    assert rows[0].extra["new_content_id"] == "amzn1.dv.gti.bbbbbbbb-0000-0000-0000-000000000001"
    assert rows[1].extra["new_content_id"] == ""
    assert rows[0].extra["old_snapshot"] == "2026-11-02_1.ldjson"


def test_prime_log_episodes_only_when_asked(tmp_path: Path) -> None:
    log_path = tmp_path / "prime.log"
    log_path.write_text(LOG, encoding="utf-8")

    rows = load_rows(log_path, kinds=("Episode",))

    assert [row.title for row in rows] == ["Pilot"]


def test_csv_input_passes_extra_columns_through(tmp_path: Path) -> None:
    csv_path = tmp_path / "alerts.csv"
    csv_path.write_text(
        "content_id,title,amazon_url,alert_id\n"
        "B0HR3W4ZJG,Chef Academy,https://www.amazon.co.uk/dp/B0HR3W4ZJG,42\n",
        encoding="utf-8",
    )
    rows = load_rows(csv_path)
    assert rows[0].extra == {"alert_id": "42"}


def test_jsonl_input(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "alerts.jsonl"
    jsonl_path.write_text(json.dumps({"content_id": "B0HR3W4ZJG", "title": "x", "amazon_url": ""}) + "\n")
    assert load_rows(jsonl_path)[0].content_id == "B0HR3W4ZJG"


def _record(verdict: str, offers: list[dict], new_id: str = "") -> dict:
    return {
        "content_id": verdict + new_id + str(len(offers)),
        "title": "t",
        "amazon_url": "u",
        "new_content_id": new_id,
        "verdict": verdict,
        "offers": offers,
        "matched_signals": ["a", "b"],
    }


def test_summary_counts_false_positives() -> None:
    records = [
        _record("AVAILABLE", [{"type": "buy", "price": "£1.99"}], new_id="x"),
        _record("AVAILABLE", [{"type": "prime", "price": "included"}, {"type": "buy"}]),
        _record("UNAVAILABLE", []),
        _record("BLOCKED", []),
    ]
    summary = build_summary(records)

    assert "False positives (alert fired, content still AVAILABLE): 2" in summary
    assert "True positives (content confirmed UNAVAILABLE):          1" in summary
    assert "Not decided (REDIRECTED / BLOCKED / UNKNOWN):            1" in summary


def test_report_paths_keep_dotted_stems() -> None:
    # Regression: Path.with_suffix turned report.reclassified.csv into report.csv
    # and reclassify truncated the original run's report.jsonl.
    csv_path, jsonl_path, summary_path = report_paths(Path("out/report.reclassified.csv"))
    assert csv_path == Path("out/report.reclassified.csv")
    assert jsonl_path == Path("out/report.reclassified.jsonl")
    assert summary_path == Path("out/report.reclassified.summary.txt")
    assert report_paths(Path("out/report.jsonl"))[0] == Path("out/report.csv")


def test_reclassify_refuses_to_overwrite_input(tmp_path: Path) -> None:
    report = tmp_path / "report.jsonl"
    original = json.dumps(_record("AVAILABLE", [])) + "\n"
    report.write_text(original, encoding="utf-8")

    exit_code = main(["reclassify", str(report), "-o", str(tmp_path / "report.csv")])

    assert exit_code == 2
    assert report.read_text(encoding="utf-8") == original


def test_csv_flattens_offers_and_signals(tmp_path: Path) -> None:
    out = tmp_path / "report.csv"
    write_csv([_record("AVAILABLE", [{"type": "buy", "price": "£7.99", "scope": "SEASON", "quality": "SD"}])], out)
    text = out.read_text(encoding="utf-8")
    assert "buy:£7.99 (SEASON SD)" in text
    assert "a; b" in text
    header = text.splitlines()[0].split(",")
    assert header[:4] == ["content_id", "title", "amazon_url", "new_content_id"]
