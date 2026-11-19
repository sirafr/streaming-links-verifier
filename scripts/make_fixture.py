"""Turn a saved page (HTML or .html.gz snapshot) into a committed test fixture.

Redacts our egress IP, session IDs and identity tokens, then writes gzip.

    python scripts/make_fixture.py reports/<run>/snapshots/<id>.html.gz \
        tests/fixtures/available_buy.html.gz
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # JSON fields in the global store / hydration data.
    (re.compile(r'("customerIPAddress":)"[^"]*"'), r'\1"0.0.0.0"'),
    (re.compile(r'("customerIPV6Address":)"[^"]*"'), r'\1null'),
    (re.compile(r'("geoToken":)"[^"]*"'), r'\1"REDACTED"'),
    (re.compile(r'("identityContext":)"[^"]*"'), r'\1"REDACTED"'),
    (re.compile(r'("requestID":)"[^"]*"'), r'\1"REDACTED"'),
    # Amazon session IDs: 123-1234567-1234567 (everywhere: JSON, URLs, ue_sid).
    (re.compile(r"\b\d{3}-\d{7}-\d{7}\b"), "000-0000000-0000000"),
    # Any remaining dotted IPv4 that sits in a quoted value.
    (re.compile(r'"(?:\d{1,3}\.){3}\d{1,3}"'), '"0.0.0.0"'),
]


def read_page(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8")


def sanitize(page_html: str) -> str:
    for pattern, replacement in _REDACTIONS:
        page_html = pattern.sub(replacement, page_html)
    return page_html


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        handle.write(sanitize(read_page(source)))
    print(f"{source} -> {target} ({target.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
