# streaming-links-verifier

`prime_availability_checker` checks Amazon Prime Video "no longer offered"
alerts against the live detail pages. For each link it decides whether the
content is still **AVAILABLE** (the alert is a false positive), really
**UNAVAILABLE**, or couldn't be decided. It records the evidence for every
verdict.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Python 3.11+. Dependencies are `httpx` (with SOCKS support) and `PyYAML`.

## Usage

```bash
# Every show alert in a crawler diff log
.venv/bin/python -m prime_availability_checker check-file files/prime.log \
    -o reports/2026-11-19/report.csv

# CSV or JSONL with content_id, title, amazon_url (+ any extra columns)
.venv/bin/python -m prime_availability_checker check-file alerts.csv -o reports/run/report.csv

# One link, printed as JSON
.venv/bin/python -m prime_availability_checker check-url \
    https://www.amazon.co.uk/gp/video/detail/amzn1.dv.gti.6fa0ce00-3f39-4e9e-b1e9-e7fb03c2e43d \
    --title "Chef Academy"
```

### Inputs

| Format | Detected by | Notes |
|---|---|---|
| CSV | `.csv` | Needs `content_id` and `amazon_url` (or a bare ASIN/GTI in `content_id`). `title` is optional but enables the title sanity check. All other columns are passed through to the report. |
| JSONL | `.jsonl` / `.ndjson` | Same fields as CSV. |
| Crawler diff log | first line `Slug:` | Reads the "[Show] NEW vs OLD" section (items missing from the newer snapshot). Use `--kinds Show,Episode` to include episodes. Adds `new_content_id` when the same title appears under a different ID in the newer snapshot. |

Links can be a GTI (`amzn1.dv.gti.…`), an ASIN, `amazon.*/dp/<ASIN>`,
`amazon.*/gp/video/detail/<id>` or `primevideo.com/detail/<id>`. They are all
rewritten onto the configured marketplace host, which is
`www.amazon.co.uk` by default.

### Outputs

Passing `-o reports/run/report.csv` writes:

- `report.csv` and `report.jsonl`: one row per link. Columns: input fields,
  pass-through columns, `verdict`, `http_status`, `requested_url`, `final_url`,
  `resolved_asin`, `resolved_gti`, `page_title`, `offers`, `matched_signals`,
  `checked_at`, `fetch_method`, `error`, `snapshot_path`.
- `report.summary.txt`: counts per verdict, the false-positive count, and a
  breakdown of the alerts whose title reappeared under a new ID.
- `snapshots/<content_id>.html.gz`: the fetched page. `--snapshots problems`
  (the default) saves every row that isn't AVAILABLE, `all` saves every row,
  and `none` saves nothing.

The JSONL is written as each row finishes. If a run is interrupted,
`--resume` skips the rows already done, except BLOCKED ones, which it retries.

### Verdicts

| Verdict | When |
|---|---|
| `AVAILABLE` | The buy box has at least one offer: `prime` (included), `channel` (e.g. Hayu), `buy`, `rent`, `free` (free with ads, or Amazon's free `FVOD` tier) or `play`. |
| `UNAVAILABLE` | HTTP 404 / "Page Not Found", a buy-box message such as "This title is currently unavailable" or "No longer available in your region", or a valid detail page with no offers at all. A page whose only button is "Watch trailer" counts as having no offers. |
| `REDIRECTED` | An HTTP redirect landed on a different title ID. |
| `BLOCKED` | A captcha, HTTP 403/429/5xx, or a network failure after retries. This says nothing about the content; rerun these rows. |
| `UNKNOWN` | The page loaded but can't be trusted: served for the wrong territory, the show title doesn't match the alert, no embedded JSON (layout change), offers and an "unavailable" message on the same page, or buy-box actions we don't recognize. |

Rules are applied in this order; the docstring at the top of
`classifier.py` lists them.

**A show link landing on its season page is normal, not a redirect.** Amazon
serves a show GTI as its latest season's page with HTTP 200, and the
canonical URL is the season ASIN. That ASIN is reported as `resolved_asin`.

**Prime titles show up as `prime:included`.** We fetch while signed out, so a
Prime-included title shows "Join Prime" instead of "Play".

**Season box first, episode list as a fallback.** Offers come from the
season-level buy box. When that box only has a free sample (e.g. "First
episode free"), the episode list's buy boxes are read too. Offers found there
are tagged `episode_level_offers`. Messages from individual episodes are
never used, so one unavailable episode can't mark a whole season
unavailable.

## Fetching

- Plain `httpx`, no headless browser. Prime Video detail pages are
  server-rendered: the whole buy box is in the
  `<script id="dv-web-page-hydration-data">` JSON in the initial HTML. If
  Amazon ever moves it to client-side rendering, rows come back `UNKNOWN`
  with `no_hydration_json` instead of being guessed. That is the point to add
  a browser fetcher behind `PlatformChecker.fetch`.
- Pacing: `--concurrency 3`, plus a random gap of `--delay-min 1.5` to
  `--delay-max 4.0` seconds between request starts, shared by all workers.
- Retries: up to `--max-attempts 4` on timeouts, connection errors and 5xx,
  with exponential backoff.
- Blocks: captchas are never solved or bypassed. A blocked row pauses all
  requests for `--block-cooldown 60` seconds and doubles the delay (up to
  8×). The delay then recovers as clean responses come in. After the main
  pass, BLOCKED rows get `--blocked-retries 1` more attempts.
- Proxy: use `--proxy http://user:pass@host:port` (or `socks5://…`), or set
  `PAC_PROXY`. The standard `HTTPS_PROXY` / `ALL_PROXY` variables also work.
  Results depend on the egress region. Every page records the territory it
  was served for, and a page whose territory doesn't match the marketplace
  is marked `UNKNOWN`.

## Updating `rules.yaml`

`prime_availability_checker/rules.yaml` holds every pattern, JSON path and
action mapping. When Amazon changes the page:

1. Run with `--snapshots all`, or find the affected rows' `snapshot_path`.
2. Look at the page's hydration JSON. The buy box is under
   `hydration.actions_path`. Each action has an `actionType` and a JSON
   `actionMetadata` (`subscriptionType`, `transactionType`, `offerFamily`,
   `benefitId`, `label`).
3. Edit the rules. Common cases:
   - a new action type shows up in `unrecognized_action:<TYPE>` signals:
     add it to the right list under `actions:` (or to `ignore_types`);
   - new "gone" wording: add it to `unavailable_messages`;
   - a non-offer button is being counted as an offer (as trailers were): add
     its `videoMaterialType` to `ignore_material_types` or its label to
     `ignore_label_patterns`;
   - a new free tier: add its `benefitId` to `free_benefit_ids`;
   - a new captcha variant: add a marker to `blocked.text_patterns`.
     **Don't** add "automated access" there: the normal 404 page contains it.
   - JSON moved: update the paths under `hydration:`.
4. Re-score the earlier run offline, without refetching:
   ```bash
   .venv/bin/python -m prime_availability_checker reclassify reports/run/report.jsonl
   ```
   This writes `report.reclassified.csv/.jsonl/.summary.txt` and logs each
   verdict that changed.
5. Add a fixture for the new case and run the tests (see below).

Use `--rules my-rules.yaml` to try a modified copy without editing the bundled
file.

## Tests

```bash
.venv/bin/python -m pytest          # offline, against tests/fixtures/
.venv/bin/python -m pytest --live   # also checks a few known links on amazon.co.uk
```

Fixtures are real UK pages captured on 2026-11-19 and gzipped. Our IP,
session IDs and identity tokens are redacted. To add one:

```bash
.venv/bin/python scripts/make_fixture.py reports/run/snapshots/<id>.html.gz tests/fixtures/<name>.html.gz
```

`blocked_captcha_SYNTHETIC.html.gz` is the one hand-written fixture: no
captcha was seen while building this. Replace it with a real capture when
one turns up (look for `captcha:` in `matched_signals`).

**Don't commit raw snapshots from `reports/`.** They contain the egress IP
and session IDs. `reports/` is gitignored.

## Adding another platform

Subclass `PlatformChecker` (in `checker.py`) and implement `build_url`,
`fetch`, `parse` and `classify`. Optionally implement `on_result` to back off
after blocks. The runner, report writer and `reclassify` work with any checker.

## Layout

```
prime_availability_checker/
  cli.py          check-file / check-url / reclassify
  checker.py      PlatformChecker interface + PrimeVideoChecker
  fetcher.py      httpx client, pacing, retries, block back-off
  parser.py       hydration JSON -> IDs, offers, messages
  classifier.py   signals -> verdict
  inputs.py       CSV / JSONL / crawler diff log readers
  report.py       CSV + JSONL + summary
  runner.py       concurrency, snapshots, BLOCKED retry pass
  rules.py        rules.yaml loader
  rules.yaml      detection rules
scripts/make_fixture.py   sanitize a snapshot into a test fixture
tests/                    offline parser/classifier tests + --live smoke test
```
