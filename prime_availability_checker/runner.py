"""Run a checker over many rows: concurrency, snapshots, blocked retries."""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .checker import PlatformChecker
from .models import CheckResult, FetchResult, InputRow, Verdict
from .report import JsonlAppender

log = logging.getLogger(__name__)

SNAPSHOT_MODES = ("none", "problems", "all")
PROBLEM_VERDICTS = {Verdict.UNAVAILABLE, Verdict.UNKNOWN, Verdict.BLOCKED, Verdict.REDIRECTED}
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True)
class SnapshotStore:
    directory: Path | None
    mode: str = "problems"

    def should_save(self, verdict: Verdict) -> bool:
        if self.directory is None or self.mode == "none":
            return False
        if self.mode == "all":
            return True
        return verdict in PROBLEM_VERDICTS

    def save(self, result: CheckResult, fetch: FetchResult) -> str | None:
        if fetch.html is None or not self.should_save(result.verdict):
            return None
        assert self.directory is not None
        name = _UNSAFE_FILENAME_RE.sub("_", result.row.content_id) or "row"
        path = self.directory / f"{name}.html.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(fetch.html)
        return str(path)


async def run_checks(
    rows: list[InputRow],
    checker: PlatformChecker,
    appender: JsonlAppender,
    snapshots: SnapshotStore,
    concurrency: int,
    blocked_retry_passes: int = 1,
) -> list[CheckResult]:
    results: dict[int, CheckResult] = {}
    pending = list(range(len(rows)))

    for pass_number in range(blocked_retry_passes + 1):
        if not pending:
            break
        if pass_number > 0:
            log.warning("Retry pass %d for %d BLOCKED row(s)", pass_number, len(pending))
        await _run_pass(rows, pending, checker, appender, snapshots, concurrency, results)
        pending = [index for index in pending if results[index].verdict is Verdict.BLOCKED]

    return [results[index] for index in range(len(rows)) if index in results]


async def _run_pass(
    rows: list[InputRow],
    indexes: list[int],
    checker: PlatformChecker,
    appender: JsonlAppender,
    snapshots: SnapshotStore,
    concurrency: int,
    results: dict[int, CheckResult],
) -> None:
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    total = len(indexes)

    async def worker(index: int) -> None:
        nonlocal done
        row = rows[index]
        async with semaphore:
            try:
                result, fetch = await checker.check(row)
            except Exception as exc:  # keep going; one bad row must not kill the run
                log.exception("Unexpected error checking %s", row.content_id)
                url = row.amazon_url
                fetch = FetchResult(url, None, None, None, error=f"internal: {exc}")
                result = checker.evaluate(row, fetch)

        checker.on_result(result)
        result.snapshot_path = snapshots.save(result, fetch)
        appender.write(result)
        results[index] = result
        done += 1
        offers = ", ".join(offer.short() for offer in result.offers[:3])
        log.info(
            "[%d/%d] %-11s %s | %s",
            done,
            total,
            result.verdict,
            row.title[:50],
            offers or "; ".join(result.matched_signals[-2:]),
        )

    await asyncio.gather(*(worker(index) for index in indexes))
