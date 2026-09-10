# Non-Interactive Spider CLI Design

## Context

The current project spreads one workflow across interactive menu scripts, repeats progress and help text, and requires nested `input()` prompts. The refactor will follow the command-oriented style of `stock_info/event-platform/scripts/dev`: one executable entry point, explicit subcommands, project-root path resolution, early validation, concise output, and meaningful exit codes.

## Goals

- Provide one executable command at `scripts/spider`.
- Remove all interactive prompts and menus.
- Make potentially broad crawling explicit through `crawl --all`.
- Keep authentication, crawling, export, and statistics as the only public capabilities.
- Preserve the existing device registry and downloaded data layout.
- Keep runtime output concise while retaining actionable failures.
- Retain test coverage without preserving obsolete implementation interfaces.

## Non-Goals

- Automating browser login or CAPTCHA handling.
- Changing the remote API, power item tag, or sampling period.
- Migrating or deleting existing JSON and CSV datasets.
- Introducing a database, scheduler, web interface, or background process manager.
- Paginating the remote history request when the API already accepts a date range.

## Public CLI

The supported commands are:

```bash
scripts/spider check [--device DEVICE_ID]
scripts/spider crawl --all [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--force]
scripts/spider crawl --area SERVICE_AREA [--area ANOTHER_AREA] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--force]
scripts/spider crawl --device DEVICE_ID [--device ANOTHER_ID] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--force]
scripts/spider export
scripts/spider stats
scripts/spider help
```

`crawl` requires exactly one selector kind: `--all`, one or more repeated `--area VALUE` flags, or one or more repeated `--device VALUE` flags. Area and device selectors cannot be mixed. A bare `scripts/spider crawl` is an argument error and never starts a full crawl. Device selectors derive their service area from `data/deviceId_info_all.csv`; callers do not provide the area separately.

The default start date is `2022-01-01`. The default end date is the local current date. Dates must be valid ISO dates and the start date must not be later than the end date.

By default, a target is skipped when its output JSON exists, is non-empty, parses successfully, and contains `code: 200`. `--force` replaces matching target files after a successful response. A failed response never overwrites existing data.

`check` uses the requested device or the first registry device and makes one small history request. It validates that configuration resolution succeeds, `IOT_COOKIE` is present, the Cookie header contains parseable pairs, the endpoint is reachable, and the response code is 200. It does not save response data. A missing `.env` is allowed when the process environment provides `IOT_COOKIE`.

`export` performs both stages in one command: convert valid device JSON responses into per-device CSV files, then outer-join those files on `time` into `data/csv/merged_power_data.csv`. It also writes `data/csv/deviceId_info.csv`.

`stats` reports registry devices and areas, successful JSON files, per-device CSV files, generated data size, and output timestamp coverage when a merged file exists.

## Architecture

### `scripts/spider`

An executable Bash wrapper. It uses `set -euo pipefail`, resolves the project root from its own path, chooses `${SPIDER_PYTHON:-python3}`, verifies that the interpreter exists, and executes `spider_cli.py` with the original arguments. It contains no business logic and does not source `.env` in the shell.

### `spider_cli.py`

Owns the `argparse` command tree, date and selector validation, command dispatch, human-readable output, and exit-code mapping. It loads settings once and calls the crawler or dataset services. It contains no HTTP or pandas transformation logic.

### `settings.py`

Defines project paths and loads `.env` with `python-dotenv` using `override=False`, so an existing process-level `IOT_COOKIE` wins. It returns an immutable settings object containing the API URL, device registry path, raw and CSV directories, Cookie value, request timeout, and fixed request delay. It never prints secret values.

### `crawler.py`

Contains the device registry loader, target selection, HTTP client, saved-response validation, and crawl loop. The registry loader accepts UTF-8 with BOM first and GBK as a fallback. Cookie pairs are parsed by splitting each semicolon-delimited item on the first equals sign and installed into one `requests.Session`.

Each device uses one GET request to `/biz/pio-microservice-biz/pio/iot-item/his/get` with `deviceId`, `startTime`, `endTime`, `periodType=10`, and `itemTag=P`. Successful JSON is written to a temporary file in the destination directory and atomically replaced into `{raw_dir}/{service_area}/{device_id}.json`.

The crawl loop continues after individual device failures, records concise failure messages, applies the configured delay only after actual requests, reports cumulative counts after each processed device through a callback, and returns a summary with total, downloaded, skipped, and failed counts.

### `dataset.py`

Contains JSON parsing, CSV export, merge, and local statistics. Valid item points are normalized to `time,value`, with timestamps parsed and values converted numerically. Malformed, unsuccessful, or empty responses are reported as failures without aborting unrelated files. Generated files use UTF-8 with BOM for spreadsheet compatibility.

## Data Flow

```text
.env + device registry
        |
        v
target selection -> authenticated API request -> atomic per-device JSON
                                                   |
                                                   v
                                       per-device time/value CSV
                                                   |
                                                   v
                                      merged power-data wide CSV
```

The existing paths remain authoritative:

- Registry: `data/deviceId_info_all.csv`
- Raw responses: `data/local/{service_area}/{device_id}.json`
- Per-device exports: `data/csv/{service_area}/{device_id}.csv`
- Merged export: `data/csv/merged_power_data.csv`
- Exported device mapping: `data/csv/deviceId_info.csv`

## Output and Exit Codes

Normal output is intentionally short. Crawl progress reports the processed and total device counts, percentage, downloaded, skipped, and failed counts. It refreshes one line when stdout is a terminal and emits one ordinary line per update when redirected to a log. Commands finish with a summary such as:

```text
crawl total=278 downloaded=12 skipped=265 failed=1
```

No banners, numbered menus, confirmations, Cookie values, or full API bodies are printed.

- Exit `0`: command completed without operational failures.
- Exit `1`: authentication, network, API, crawl, or export failures occurred.
- Exit `2`: invalid arguments, missing `IOT_COOKIE`, unreadable configuration, missing registry, or missing required dependency.

For multi-device crawling and export, processing continues where safe and exits `1` when the final failed count is non-zero.

## Security

- `.env` remains local and ignored by Git.
- Runtime environment variables override `.env` values.
- Cookie values are never logged, included in exceptions shown to users, or written outside `.env`.
- The refactor does not recreate `cookies.json`, `config.json`, hard-coded credentials, or browser-login helpers.

## Migration

The refactor creates:

- `scripts/spider`
- `spider_cli.py`
- `settings.py`
- `crawler.py`
- `dataset.py`

It removes these obsolete implementation files after their behavior has been replaced:

- `run.py`
- `batch_crawl.py`
- `manual_login.py`
- `parse_data.py`
- `env_settings.py`
- `iot_spider.py`

The README and dependencies are rewritten around the new command interface. Existing `.env`, `data/deviceId_info_all.csv`, `data/local`, and `data/csv` remain in place.

## Testing

Tests use `unittest`, temporary directories, and injected HTTP sessions. They cover:

- command parsing and required mutually exclusive selectors;
- `.env` and process-environment precedence without exposing secrets;
- UTF-8 and GBK registry loading;
- area and device target selection;
- Cookie header construction and local failure when no Cookie exists;
- successful API requests, 401 responses, network failures, and malformed JSON;
- resume skipping, `--force`, atomic output, and failure summaries;
- JSON-to-CSV conversion, merged export, and statistics;
- CLI exit codes and concise output;
- terminal and redirected-log progress rendering;
- wrapper operation from a working directory outside the project.

No automated test calls the real service. A final live `check` may be run only as an explicit integration verification and does not save data.

## Acceptance Criteria

- Every supported operation is available from `scripts/spider` with no `input()` calls.
- `crawl --all` is required for a full crawl; bare `crawl` fails before any request.
- Existing valid JSON is skipped unless `--force` is supplied.
- Existing datasets remain readable and retain their current locations.
- Missing or rejected Cookie configuration produces a short actionable error and a non-zero exit code.
- Old interactive scripts and duplicate implementations are removed.
- Offline tests and syntax checks pass.
