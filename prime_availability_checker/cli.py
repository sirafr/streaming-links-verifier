"""Command-line entry point.

    python -m prime_availability_checker check-file alerts.csv -o reports/run/report.csv
    python -m prime_availability_checker check-url https://www.amazon.co.uk/gp/video/detail/<id>
    python -m prime_availability_checker reclassify reports/run/report.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import sys
from pathlib import Path

from .checker import PrimeVideoChecker
from .fetcher import FetchSettings
from .inputs import load_rows
from .models import FetchResult, InputRow
from .report import JsonlAppender, finalize, latest_per_content, read_jsonl, report_paths
from .rules import load_rules
from .runner import SNAPSHOT_MODES, SnapshotStore, run_checks

log = logging.getLogger("prime_availability_checker")


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rules", type=Path, help="Path to rules.yaml (default: bundled)")
    parser.add_argument("--marketplace", help="Marketplace key from rules.yaml (default: uk)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")


def _add_fetch_options(parser: argparse.ArgumentParser) -> None:
    defaults = FetchSettings()
    parser.add_argument("--concurrency", type=int, default=defaults.concurrency)
    parser.add_argument(
        "--delay-min", type=float, default=defaults.delay_min,
        help="Min seconds between request starts (shared across workers)",
    )
    parser.add_argument("--delay-max", type=float, default=defaults.delay_max)
    parser.add_argument("--timeout", type=float, default=defaults.timeout)
    parser.add_argument("--max-attempts", type=int, default=defaults.max_attempts)
    parser.add_argument(
        "--block-cooldown", type=float, default=defaults.block_cooldown,
        help="Seconds to pause all requests after a captcha/throttle",
    )
    parser.add_argument(
        "--proxy", default=os.environ.get("PAC_PROXY"),
        help="Proxy URL (http://, https://, socks5://). Defaults to $PAC_PROXY; "
        "HTTPS_PROXY/ALL_PROXY are also honored when unset.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prime_availability_checker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    check_file = commands.add_parser("check-file", help="Check every link in a CSV/JSONL/log file")
    check_file.add_argument("input", type=Path)
    check_file.add_argument("-o", "--output", type=Path, required=True,
                            help="Report path; .csv, .jsonl and .summary.txt are written side by side")
    check_file.add_argument("--format", default="auto", choices=["auto", "csv", "jsonl", "prime-log"])
    check_file.add_argument("--kinds", default="Show",
                            help="prime-log only: comma-separated kinds to read (Show,Episode)")
    check_file.add_argument("--limit", type=int, help="Only check the first N rows")
    check_file.add_argument("--snapshots", default="problems", choices=SNAPSHOT_MODES,
                            help="Save page HTML for: problems (not AVAILABLE), all, or none")
    check_file.add_argument("--snapshots-dir", type=Path,
                            help="Default: <output dir>/snapshots")
    check_file.add_argument("--resume", action="store_true",
                            help="Skip rows already in the JSONL report (except BLOCKED ones)")
    check_file.add_argument("--blocked-retries", type=int, default=1,
                            help="Extra passes over BLOCKED rows at the end")
    _add_common_options(check_file)
    _add_fetch_options(check_file)

    check_url = commands.add_parser("check-url", help="Check a single link and print the result")
    check_url.add_argument("url")
    check_url.add_argument("--title", default="", help="Expected title, enables the title check")
    _add_common_options(check_url)
    _add_fetch_options(check_url)

    reclassify = commands.add_parser(
        "reclassify", help="Re-run parse+classify on a previous run's snapshots (no network)")
    reclassify.add_argument("report", type=Path, help="report.jsonl from an earlier run")
    reclassify.add_argument("-o", "--output", type=Path,
                            help="Where to write the new report (default: <report>.reclassified.csv)")
    _add_common_options(reclassify)

    return parser


def _settings_from(args: argparse.Namespace) -> FetchSettings:
    return FetchSettings(
        concurrency=args.concurrency,
        delay_min=args.delay_min,
        delay_max=max(args.delay_max, args.delay_min),
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        block_cooldown=args.block_cooldown,
        proxy=args.proxy or None,
    )


async def _check_file(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    marketplace = rules.marketplace(args.marketplace)
    kinds = tuple(kind.strip() for kind in args.kinds.split(",") if kind.strip())
    rows = load_rows(args.input, args.format, kinds=kinds)
    if args.limit:
        rows = rows[: args.limit]

    csv_path, jsonl_path, summary_path = report_paths(args.output)
    if args.resume:
        finished = {
            record["content_id"]
            for record in latest_per_content(read_jsonl(jsonl_path))
            if record["verdict"] != "BLOCKED"
        }
        skipped = sum(1 for row in rows if row.content_id in finished)
        rows = [row for row in rows if row.content_id not in finished]
        log.info("Resuming: %d row(s) already done, %d to check", skipped, len(rows))

    snapshots_dir = args.snapshots_dir or args.output.parent / "snapshots"
    snapshots = SnapshotStore(snapshots_dir, args.snapshots)
    settings = _settings_from(args)
    log.info(
        "Checking %d row(s) on %s (territory %s), concurrency %d, delay %.1f-%.1fs, proxy %s",
        len(rows), marketplace.host, marketplace.territory, settings.concurrency,
        settings.delay_min, settings.delay_max, "on" if settings.proxy else "off",
    )

    appender = JsonlAppender(jsonl_path, append=args.resume)
    try:
        async with PrimeVideoChecker(rules, marketplace, settings) as checker:
            await run_checks(rows, checker, appender, snapshots, settings.concurrency,
                             blocked_retry_passes=args.blocked_retries)
    finally:
        appender.close()

    summary = finalize(jsonl_path, csv_path, summary_path)
    print(summary)
    print(f"Report: {csv_path}\n        {jsonl_path}\nSummary: {summary_path}")
    return 0


async def _check_url(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    marketplace = rules.marketplace(args.marketplace)
    row = InputRow(content_id=PrimeVideoChecker.extract_title_id(args.url) or args.url,
                   title=args.title, amazon_url=args.url)
    async with PrimeVideoChecker(rules, marketplace, _settings_from(args)) as checker:
        result, _ = await checker.check(row)
    print(json.dumps(result.to_record(), indent=2, ensure_ascii=False))
    return 0


def _reclassify(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    marketplace = rules.marketplace(args.marketplace)
    checker = PrimeVideoChecker(rules, marketplace)

    output = args.output or args.report.with_name(args.report.stem + ".reclassified.csv")
    csv_path, jsonl_path, summary_path = report_paths(output)
    if jsonl_path.resolve() == args.report.resolve():
        log.error("Output %s would overwrite the input report; pass a different -o", jsonl_path)
        return 2
    # Read everything before opening the output, so nothing can truncate the input.
    records = latest_per_content(read_jsonl(args.report))
    appender = JsonlAppender(jsonl_path, append=False)
    changed = 0
    missing = 0
    try:
        for record in records:
            snapshot = record.get("snapshot_path")
            if not snapshot or not Path(snapshot).exists():
                missing += 1
                continue
            with gzip.open(snapshot, "rt", encoding="utf-8") as handle:
                page_html = handle.read()
            core = {"content_id", "title", "amazon_url"}
            extra_keys = [key for key in record if key not in core]
            # Pass-through columns are whatever preceded "verdict" in the record.
            extra = {key: record[key] for key in extra_keys[: extra_keys.index("verdict")]}
            row = InputRow(record["content_id"], record["title"], record["amazon_url"], extra)
            requested = record["requested_url"]
            final = record.get("final_url")
            fetch = FetchResult(
                requested_url=requested,
                final_url=final,
                http_status=record.get("http_status"),
                html=page_html,
                method=record.get("fetch_method") or "http",
                redirects=1 if final and final != requested else 0,
            )
            result = checker.evaluate(row, fetch)
            result.checked_at = record["checked_at"]
            result.snapshot_path = snapshot
            if str(result.verdict) != record["verdict"]:
                changed += 1
                log.info("%s: %s -> %s", record["title"], record["verdict"], result.verdict)
            appender.write(result)
    finally:
        appender.close()

    print(finalize(jsonl_path, csv_path, summary_path))
    print(f"{changed} verdict(s) changed; {missing} row(s) had no snapshot and were skipped")
    print(f"Report: {csv_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "check-file":
        return asyncio.run(_check_file(args))
    if args.command == "check-url":
        return asyncio.run(_check_url(args))
    if args.command == "reclassify":
        return _reclassify(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
