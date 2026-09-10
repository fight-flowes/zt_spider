# Non-Interactive Spider CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the interactive spider scripts with one explicit, concise `scripts/spider` command that checks authentication, crawls selected devices, exports datasets, and reports local statistics.

**Architecture:** A thin Bash wrapper invokes `spider_cli.py`. The CLI delegates configuration to `settings.py`, acquisition to `crawler.py`, and local transformation/statistics to `dataset.py`; existing data paths and formats remain unchanged.

**Tech Stack:** Python 3.10+, Bash, `requests`, `pandas`, `python-dotenv`, standard-library `argparse` and `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-09-noninteractive-spider-cli-design.md`

## Global Constraints

- There is no `input()` call or interactive confirmation.
- A full crawl requires the literal `crawl --all` selector.
- Existing `.env`, device registry, JSON, and CSV data remain in place.
- Process environment values override `.env` values.
- Cookie values never appear in output or displayed exceptions.
- Existing successful JSON is skipped unless `--force` is present.
- Individual device failures do not stop unrelated devices.
- Automated tests never call the live service.

---

### Task 1: Runtime settings

**Files:**
- Create: `settings.py`
- Modify: `requirements.txt`
- Create: `tests/test_settings.py`

**Interfaces:**
- Consumes: project root, `.env`, and process environment.
- Produces: `Settings` and `load_settings(root: Path | None = None) -> Settings`.

- [ ] **Step 1: Write failing settings tests**

```python
class SettingsTests(unittest.TestCase):
    def test_process_cookie_overrides_dotenv_cookie(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("IOT_COOKIE=file-cookie\n", encoding="utf-8")
            with patch.dict(os.environ, {"IOT_COOKIE": "process-cookie"}, clear=True):
                settings = load_settings(root)
        self.assertEqual(settings.cookie, "process-cookie")

    def test_paths_are_rooted_at_project(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(root)
        self.assertEqual(settings.registry_path, root / "data/deviceId_info_all.csv")
        self.assertEqual(settings.raw_dir, root / "data/local")
        self.assertEqual(settings.csv_dir, root / "data/csv")
```

- [ ] **Step 2: Verify the tests fail because `settings.py` is absent**

Run: `python3 -m unittest tests/test_settings.py -v`

- [ ] **Step 3: Implement immutable settings and dependency**

```python
@dataclass(frozen=True)
class Settings:
    root: Path
    api_url: str
    registry_path: Path
    raw_dir: Path
    csv_dir: Path
    cookie: str | None
    timeout: int = 120
    request_delay: float = 0.2

def load_settings(root: Path | None = None) -> Settings:
    project_root = (root or Path(__file__).resolve().parent).resolve()
    load_dotenv(project_root / ".env", override=False)
    return Settings(
        root=project_root,
        api_url="https://ztx.zcgc.cn/biz/pio-microservice-biz/pio/iot-item/his/get",
        registry_path=project_root / "data/deviceId_info_all.csv",
        raw_dir=project_root / "data/local",
        csv_dir=project_root / "data/csv",
        cookie=os.environ.get("IOT_COOKIE") or None,
    )
```

Add `python-dotenv` to `requirements.txt`.

- [ ] **Step 4: Verify settings tests pass**

Run: `python3 -m unittest tests/test_settings.py -v`

- [ ] **Step 5: Commit the settings slice**

```bash
git add zt_analyse/code/spider/settings.py zt_analyse/code/spider/requirements.txt zt_analyse/code/spider/tests/test_settings.py
git commit -m "refactor: centralize spider settings"
```

### Task 2: Device selection and crawler core

**Files:**
- Create: `crawler.py`
- Create: `tests/test_crawler.py`

**Interfaces:**
- Consumes: `Settings`, ISO `date` objects, selector values, and an optional injected HTTP session.
- Produces: `Device`, `CrawlSummary`, `SpiderError`, `AuthenticationError`, `load_devices`, `select_devices`, `IoTClient.fetch_history`, and `crawl_devices`.

- [ ] **Step 1: Write failing registry and selector tests**

```python
def test_utf8_registry_loads(self):
    path.write_text("deviceId,service_area\n10001,测试服务区\n", encoding="utf-8")
    self.assertEqual(load_devices(path), [Device("10001", "测试服务区")])

def test_selector_is_required_and_exclusive(self):
    with self.assertRaises(ValueError):
        select_devices(self.devices)
    with self.assertRaises(ValueError):
        select_devices(self.devices, all_devices=True, areas=["测试服务区"])

def test_device_selection_uses_registry_area(self):
    self.assertEqual(
        select_devices(self.devices, device_ids=["10001"]),
        [Device("10001", "测试服务区")],
    )
```

Add a GBK fixture with the same literal row and assert the same `Device`.

- [ ] **Step 2: Verify registry tests fail because `crawler.py` is absent**

Run: `python3 -m unittest tests/test_crawler.py -v`

- [ ] **Step 3: Implement registry loading and target selection**

```python
@dataclass(frozen=True)
class Device:
    device_id: str
    service_area: str

def load_devices(path: Path) -> list[Device]:
    decode_error = None
    for encoding in ("utf-8-sig", "gbk"):
        try:
            with path.open(encoding=encoding, newline="") as source:
                return [
                    Device(row["deviceId"].strip(), row["service_area"].strip())
                    for row in csv.DictReader(source)
                ]
        except UnicodeDecodeError as error:
            decode_error = error
    raise ValueError(f"cannot decode device registry: {decode_error}")

def select_devices(
    devices: list[Device],
    *,
    all_devices: bool = False,
    areas: list[str] | None = None,
    device_ids: list[str] | None = None,
) -> list[Device]:
    selectors = int(all_devices) + int(bool(areas)) + int(bool(device_ids))
    if selectors != 1:
        raise ValueError("choose exactly one of --all, --area, or --device")
    # Preserve registry order; validate requested areas or IDs before returning.
```

The final branch validates unknown values and raises `ValueError` containing only unknown selectors.

- [ ] **Step 4: Verify selector tests pass**

Run: `python3 -m unittest tests/test_crawler.py -v`

- [ ] **Step 5: Write failing HTTP, resume, force, and preservation tests**

```python
def test_client_returns_successful_response_and_sets_expected_params(self):
    result = client.fetch_history("10001", date(2026, 1, 1), date(2026, 1, 2))
    self.assertEqual(result["code"], 200)
    self.assertEqual(session.last_params["deviceId"], "10001")
    self.assertEqual(session.last_params["periodType"], 10)
    self.assertEqual(session.last_params["itemTag"], "P")

def test_unauthorized_response_raises_authentication_error(self):
    with self.assertRaises(AuthenticationError):
        client.fetch_history("10001", date(2026, 1, 1), date(2026, 1, 1))

def test_valid_existing_file_is_skipped(self):
    summary = crawl_devices(client, devices, raw_dir, start, end)
    self.assertEqual(summary, CrawlSummary(1, 0, 1, 0, ()))

def test_force_replaces_a_valid_existing_file(self):
    summary = crawl_devices(client, devices, raw_dir, start, end, force=True, delay=0)
    self.assertEqual(summary.downloaded, 1)
    self.assertEqual(json.loads(destination.read_text())["data"], new_data)

def test_failed_force_fetch_keeps_the_previous_file(self):
    before = destination.read_bytes()
    summary = crawl_devices(failing_client, devices, raw_dir, start, end, force=True, delay=0)
    self.assertEqual(destination.read_bytes(), before)
    self.assertEqual(summary.failed, 1)
```

The fake response includes `status_code`, `raise_for_status()`, and `json()`; the fake session records URL, parameters, and timeout without making a network call.

- [ ] **Step 6: Verify the new crawler tests fail for missing behavior**

Run: `python3 -m unittest tests/test_crawler.py -v`

- [ ] **Step 7: Implement HTTP client and crawl loop**

```python
class IoTClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        if not settings.cookie:
            raise ValueError("IOT_COOKIE is not configured")
        self.settings = settings
        self.session = session or requests.Session()
        pairs = parse_cookie_header(settings.cookie)
        if not pairs:
            raise ValueError("IOT_COOKIE contains no valid cookie pairs")
        self.session.cookies.update(pairs)

    def fetch_history(self, device_id: str, start: date, end: date) -> dict:
        params = {
            "deviceId": device_id,
            "startTime": f"{start.isoformat()} 00:00:00",
            "endTime": f"{end.isoformat()} 23:59:59",
            "periodType": 10,
            "itemTag": "P",
        }
        # GET with Settings.timeout; map 401, request errors, invalid JSON,
        # and non-200 API payloads to secret-free SpiderError subclasses.
```

```python
@dataclass(frozen=True)
class CrawlSummary:
    total: int
    downloaded: int
    skipped: int
    failed: int
    errors: tuple[str, ...]

def crawl_devices(
    client: IoTClient,
    devices: list[Device],
    raw_dir: Path,
    start: date,
    end: date,
    *,
    force: bool = False,
    delay: float = 0.2,
) -> CrawlSummary:
    # For each device: validate existing output, fetch when required, write a
    # sibling temporary JSON, then os.replace it. Continue after SpiderError.
```

- [ ] **Step 8: Verify all crawler tests pass**

Run: `python3 -m unittest tests/test_crawler.py -v`

- [ ] **Step 9: Commit the crawler slice**

```bash
git add zt_analyse/code/spider/crawler.py zt_analyse/code/spider/tests/test_crawler.py
git commit -m "refactor: isolate spider crawl engine"
```

### Task 3: Dataset export and statistics

**Files:**
- Create: `dataset.py`
- Create: `tests/test_dataset.py`

**Interfaces:**
- Consumes: raw JSON directory, CSV directory, and registry path.
- Produces: `ExportSummary`, `DatasetStats`, `export_dataset`, and `collect_stats`.

- [ ] **Step 1: Write failing export tests**

```python
def test_export_writes_device_csv_merged_csv_and_mapping(self):
    summary = export_dataset(raw_dir, csv_dir)
    self.assertEqual(summary.discovered, 2)
    self.assertEqual(summary.converted, 2)
    merged = pd.read_csv(csv_dir / "merged_power_data.csv")
    self.assertEqual(list(merged.columns), ["time", "10001_value", "10002_value"])
    self.assertEqual(len(merged), 3)
    mapping = pd.read_csv(csv_dir / "deviceId_info.csv")
    self.assertEqual(len(mapping), 2)

def test_export_counts_malformed_and_empty_responses(self):
    summary = export_dataset(raw_dir, csv_dir)
    self.assertEqual(summary.converted, 1)
    self.assertEqual(summary.failed, 2)
```

Fixtures use literal API responses with two overlapping timestamps.

- [ ] **Step 2: Verify export tests fail because `dataset.py` is absent**

Run: `python3 -m unittest tests/test_dataset.py -v`

- [ ] **Step 3: Implement one-pass export**

```python
@dataclass(frozen=True)
class ExportSummary:
    discovered: int
    converted: int
    failed: int
    rows: int
    columns: int
    errors: tuple[str, ...]

def export_dataset(raw_dir: Path, csv_dir: Path) -> ExportSummary:
    # Read sorted service-area JSON files. For each valid code=200 response,
    # normalize itemPoints to time/value, write its CSV, and retain a
    # device-named time-indexed Series. Concatenate all Series once with an
    # outer join, sort time, and write merged_power_data.csv and mapping CSV.
```

All generated CSV files use `utf-8-sig`. An invalid file increments `failed` and does not abort other files.

- [ ] **Step 4: Verify export tests pass**

Run: `python3 -m unittest tests/test_dataset.py -v`

- [ ] **Step 5: Write failing statistics test**

```python
def test_collect_stats_reports_counts_size_and_coverage(self):
    stats = collect_stats(registry_path, raw_dir, csv_dir)
    self.assertEqual(stats.registry_devices, 2)
    self.assertEqual(stats.registry_areas, 1)
    self.assertEqual(stats.json_files, 1)
    self.assertEqual(stats.csv_files, 1)
    self.assertEqual(stats.first_timestamp, "2026-01-01 00:00:00")
    self.assertEqual(stats.last_timestamp, "2026-01-01 00:15:00")
```

- [ ] **Step 6: Implement statistics**

```python
@dataclass(frozen=True)
class DatasetStats:
    registry_devices: int
    registry_areas: int
    json_files: int
    csv_files: int
    data_bytes: int
    first_timestamp: str | None
    last_timestamp: str | None

def collect_stats(registry_path: Path, raw_dir: Path, csv_dir: Path) -> DatasetStats:
    # Count unique registry devices/areas, successful raw JSON files,
    # per-device CSV files, total generated bytes, and merged time coverage.
```

- [ ] **Step 7: Verify dataset tests and commit**

Run: `python3 -m unittest tests/test_dataset.py -v`

```bash
git add zt_analyse/code/spider/dataset.py zt_analyse/code/spider/tests/test_dataset.py
git commit -m "refactor: consolidate spider data pipeline"
```

### Task 4: CLI and executable wrapper

**Files:**
- Create: `spider_cli.py`
- Create: `scripts/spider`
- Create: `tests/test_cli.py`
- Create: `tests/test_wrapper.py`

**Interfaces:**
- Consumes: `load_settings`, crawler APIs, dataset APIs, and argv.
- Produces: `build_parser() -> argparse.ArgumentParser`, `main(argv: Sequence[str] | None = None) -> int`, and executable `scripts/spider`.

- [ ] **Step 1: Write failing parser tests**

```python
def test_crawl_requires_selector(self):
    with self.assertRaises(SystemExit):
        build_parser().parse_args(["crawl"])

def test_crawl_rejects_mixed_selectors(self):
    with self.assertRaises(SystemExit):
        build_parser().parse_args(["crawl", "--all", "--device", "10001"])

def test_repeated_device_selector_is_supported(self):
    args = build_parser().parse_args(
        ["crawl", "--device", "10001", "--device", "10002"]
    )
    self.assertEqual(args.device_ids, ["10001", "10002"])
```

- [ ] **Step 2: Verify parser tests fail because `spider_cli.py` is absent**

Run: `python3 -m unittest tests/test_cli.py -v`

- [ ] **Step 3: Implement parser and command dispatch**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/spider")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("help")
    check = subcommands.add_parser("check")
    check.add_argument("--device")
    crawl = subcommands.add_parser("crawl")
    selectors = crawl.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--all", action="store_true", dest="all_devices")
    selectors.add_argument("--area", action="append", dest="areas")
    selectors.add_argument("--device", action="append", dest="device_ids")
    crawl.add_argument("--start", type=iso_date, default=date(2022, 1, 1))
    crawl.add_argument("--end", type=iso_date, default=date.today())
    crawl.add_argument("--force", action="store_true")
    subcommands.add_parser("export")
    subcommands.add_parser("stats")
    return parser

def main(argv: Sequence[str] | None = None) -> int:
    # Load settings, dispatch one command, print concise summaries, and map
    # argument/configuration failures to 2 and operational failures to 1.
```

- [ ] **Step 4: Write failing command-level tests**

```python
def invoke_cli(self, argv, settings):
    output = io.StringIO()
    errors = io.StringIO()
    with (
        patch("spider_cli.load_settings", return_value=settings),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        code = main(argv)
    return code, output.getvalue(), errors.getvalue()

def test_missing_cookie_returns_two_without_request(self):
    code, output, errors = self.invoke_cli(["check"], self.settings(cookie=None))
    self.assertEqual(code, 2)
    self.assertEqual(output, "")
    self.assertIn("IOT_COOKIE", errors)

@patch("spider_cli.load_devices", return_value=[Device("10001", "测试服务区")])
@patch("spider_cli.IoTClient")
def test_check_success_returns_zero(self, client_type, _load_devices):
    client_type.return_value.fetch_history.return_value = {"code": 200, "data": {}}
    code, output, errors = self.invoke_cli(["check"], self.settings(cookie="SESSION=value"))
    self.assertEqual(code, 0)
    self.assertIn("check ok", output)
    self.assertEqual(errors, "")

@patch("spider_cli.crawl_devices")
@patch("spider_cli.select_devices", return_value=[Device("10001", "测试服务区")])
@patch("spider_cli.load_devices", return_value=[Device("10001", "测试服务区")])
@patch("spider_cli.IoTClient")
def test_partial_crawl_failure_returns_one_and_prints_summary(
    self, _client_type, _load_devices, _select_devices, crawl
):
    crawl.return_value = CrawlSummary(2, 1, 0, 1, ("10002: request failed",))
    code, output, errors = self.invoke_cli(
        ["crawl", "--all"], self.settings(cookie="SESSION=value")
    )
    self.assertEqual(code, 1)
    self.assertIn("downloaded=1", output)
    self.assertIn("failed=1", output)
    self.assertIn("10002", errors)

@patch("spider_cli.export_dataset")
def test_export_failure_count_returns_one(self, export):
    export.return_value = ExportSummary(2, 1, 1, 2, 2, ("bad.json",))
    code, output, errors = self.invoke_cli(["export"], self.settings(cookie=None))
    self.assertEqual(code, 1)
    self.assertIn("converted=1", output)
    self.assertIn("bad.json", errors)

@patch("spider_cli.collect_stats")
def test_stats_does_not_require_cookie(self, collect):
    collect.return_value = DatasetStats(2, 1, 1, 1, 100, None, None)
    code, output, errors = self.invoke_cli(["stats"], self.settings(cookie=None))
    self.assertEqual(code, 0)
    self.assertIn("registry_devices=2", output)
    self.assertEqual(errors, "")
```

Each test patches a service boundary, invokes `main(argv)`, and asserts the returned exit code plus visible output. It never asserts that a mock merely exists.

- [ ] **Step 5: Complete dispatch and verify CLI tests**

Run: `python3 -m unittest tests/test_cli.py -v`

- [ ] **Step 6: Add and test the wrapper**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${SPIDER_PYTHON:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  printf 'error: Python interpreter not found: %s\n' "$PYTHON_BIN" >&2
  exit 2
}
exec "$PYTHON_BIN" "$ROOT/spider_cli.py" "$@"
```

The wrapper test runs `scripts/spider help` from a temporary working directory and asserts exit 0 plus `check`, `crawl`, `export`, and `stats` in stdout.

- [ ] **Step 7: Verify CLI/wrapper tests and commit**

Run: `python3 -m unittest tests/test_cli.py tests/test_wrapper.py -v`

```bash
git add zt_analyse/code/spider/spider_cli.py zt_analyse/code/spider/scripts/spider zt_analyse/code/spider/tests/test_cli.py zt_analyse/code/spider/tests/test_wrapper.py
git commit -m "refactor: add non-interactive spider command"
```

### Task 5: Remove legacy surface and document the workflow

**Files:**
- Delete: `run.py`, `batch_crawl.py`, `manual_login.py`, `parse_data.py`, `env_settings.py`, `iot_spider.py`
- Delete: the five corresponding legacy test files
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: the completed new CLI.
- Produces: one documented and tested public entry point with no obsolete runtime surface.

- [ ] **Step 1: Verify the complete new test suite before deletion**

Run: `python3 -m unittest tests/test_settings.py tests/test_crawler.py tests/test_dataset.py tests/test_cli.py tests/test_wrapper.py -v`

- [ ] **Step 2: Delete only the obsolete files listed above**

Keep `.env`, `data/deviceId_info_all.csv`, `data/local`, and `data/csv`.

- [ ] **Step 3: Rewrite README and example configuration**

Document installation, `IOT_COOKIE`, all six commands, selectors, date flags, resume/`--force`, output paths, summaries, exit codes, and the Cookie security warning. Remove numbered menus and manual-login helper instructions.

- [ ] **Step 4: Verify no interactive or legacy references remain**

Run: `rg -n "input\(|run\.py|batch_crawl|manual_login|parse_data|env_settings|iot_spider" . -g '*.py' -g '*.md' -g '!docs/superpowers/**'`

Expected: no output.

- [ ] **Step 5: Run final offline verification**

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
scripts/spider help
scripts/spider stats
```

Tests and compilation exit 0. Help lists `check`, `crawl`, `export`, and `stats`; stats reads existing data without requiring a Cookie.

- [ ] **Step 6: Commit the migration**

```bash
git add -A zt_analyse/code/spider
git commit -m "refactor: simplify spider project workflow"
```

- [ ] **Step 7: Run the optional live check when network access is approved**

Run: `scripts/spider check`

It must not write files. Report its exit code and API status without printing Cookie values.
