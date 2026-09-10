import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from crawler import CrawlSummary, Device, SpiderError
from dataset import DatasetStats, ExportSummary
from settings import Settings
from spider_cli import build_parser, main


def make_settings(root: Path, cookie="SESSION=value") -> Settings:
    return Settings(
        root=root,
        api_url="https://example.test/history",
        registry_path=root / "devices.csv",
        raw_dir=root / "local",
        csv_dir=root / "csv",
        cookie=cookie,
        timeout=17,
        request_delay=0,
    )


class TerminalBuffer(io.StringIO):
    def isatty(self):
        return True


class ParserTests(unittest.TestCase):
    def test_crawl_requires_selector(self):
        """A bare crawl must fail instead of implicitly selecting every device."""
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["crawl"])
        self.assertEqual(raised.exception.code, 2)

    def test_crawl_rejects_mixed_selectors(self):
        """Area, device, and all selectors are mutually exclusive."""
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["crawl", "--all", "--device", "10001"])
        self.assertEqual(raised.exception.code, 2)

    def test_repeated_device_and_area_selectors_are_supported(self):
        """One selector kind may be repeated to request several targets."""
        device_args = build_parser().parse_args(
            ["crawl", "--device", "10001", "--device", "10002"]
        )
        area_args = build_parser().parse_args(
            ["crawl", "--area", "A服务区", "--area", "B服务区"]
        )
        self.assertEqual(device_args.device_ids, ["10001", "10002"])
        self.assertEqual(area_args.areas, ["A服务区", "B服务区"])

    def test_invalid_iso_date_is_an_argument_error(self):
        """Invalid dates must be rejected before command dispatch."""
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["crawl", "--all", "--start", "2026-99-01"])
        self.assertEqual(raised.exception.code, 2)


class CommandTests(unittest.TestCase):
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

    def test_help_lists_the_public_commands(self):
        """The explicit help command is the complete public entry-point guide."""
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["help"], make_settings(Path(temp_dir), cookie=None)
            )
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        for command in ("check", "crawl", "export", "stats"):
            self.assertIn(command, output)

    @patch("spider_cli.IoTClient")
    def test_missing_cookie_returns_two_without_request(self, client_type):
        """Missing authentication is a local configuration error."""
        client_type.side_effect = ValueError("IOT_COOKIE is not configured")
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["check"], make_settings(Path(temp_dir), cookie=None)
            )
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("IOT_COOKIE", errors)
        client_type.return_value.fetch_history.assert_not_called()

    @patch("spider_cli.load_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.IoTClient")
    def test_check_success_returns_zero(self, client_type, _load_devices):
        """A successful one-device API probe reports its target and saves nothing."""
        client_type.return_value.fetch_history.return_value = {"code": 200, "data": {}}
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["check"], make_settings(Path(temp_dir))
            )
        self.assertEqual(code, 0)
        self.assertIn("check ok device=10001", output)
        self.assertEqual(errors, "")
        client_type.return_value.fetch_history.assert_called_once()

    @patch("spider_cli.load_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.IoTClient")
    def test_check_operational_failure_returns_one(self, client_type, _load_devices):
        """Network and API failures use the operational exit code."""
        client_type.return_value.fetch_history.side_effect = SpiderError("offline")
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["check"], make_settings(Path(temp_dir))
            )
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("offline", errors)

    @patch("spider_cli.crawl_devices")
    @patch("spider_cli.select_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.load_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.IoTClient")
    def test_partial_crawl_failure_returns_one_and_prints_summary(
        self, _client_type, _load_devices, _select_devices, crawl
    ):
        """Per-device failures remain visible after the rest of a crawl completes."""
        crawl.return_value = CrawlSummary(
            2, 1, 0, 1, ("测试服务区/10002: request failed",)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["crawl", "--all"], make_settings(Path(temp_dir))
            )
        self.assertEqual(code, 1)
        self.assertIn("crawl total=2 downloaded=1 skipped=0 failed=1", output)
        self.assertIn("10002", errors)

    @patch("spider_cli.crawl_devices")
    @patch("spider_cli.select_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.load_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.IoTClient")
    def test_redirected_crawl_output_records_one_progress_line_per_device(
        self, _client_type, _load_devices, _select_devices, crawl
    ):
        """Redirected output must remain readable and contain no terminal control flow."""
        summary = CrawlSummary(2, 1, 1, 0, ())

        def crawl_with_progress(*args, **kwargs):
            progress = kwargs.get("progress")
            if progress is not None:
                progress(1, 2, 1, 0, 0)
                progress(2, 2, 1, 1, 0)
            return summary

        crawl.side_effect = crawl_with_progress
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["crawl", "--all"], make_settings(Path(temp_dir))
            )

        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("[1/2] 50% downloaded=1 skipped=0 failed=0\n", output)
        self.assertIn("[2/2] 100% downloaded=1 skipped=1 failed=0\n", output)
        self.assertNotIn("\r", output)

    @patch("spider_cli.crawl_devices")
    @patch("spider_cli.select_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.load_devices", return_value=[Device("10001", "测试服务区")])
    @patch("spider_cli.IoTClient")
    def test_terminal_crawl_output_refreshes_one_line(
        self, _client_type, _load_devices, _select_devices, crawl
    ):
        """Interactive output must refresh in place and end before the summary."""
        summary = CrawlSummary(2, 2, 0, 0, ())

        def crawl_with_progress(*args, **kwargs):
            progress = kwargs.get("progress")
            if progress is not None:
                progress(1, 2, 1, 0, 0)
                progress(2, 2, 2, 0, 0)
            return summary

        crawl.side_effect = crawl_with_progress
        output = TerminalBuffer()
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "spider_cli.load_settings",
                    return_value=make_settings(Path(temp_dir)),
                ),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                code = main(["crawl", "--all"])

        rendered = output.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("\r[1/2] 50% downloaded=1 skipped=0 failed=0", rendered)
        self.assertIn("\r[2/2] 100% downloaded=2 skipped=0 failed=0\n", rendered)
        self.assertEqual(rendered.count("\n"), 2)

    def test_start_after_end_returns_two_before_loading_registry(self):
        """An inverted date range is rejected locally without reading devices."""
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "spider_cli.load_devices"
        ) as load_devices:
            code, output, errors = self.invoke_cli(
                [
                    "crawl",
                    "--all",
                    "--start",
                    "2026-01-02",
                    "--end",
                    "2026-01-01",
                ],
                make_settings(Path(temp_dir)),
            )
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("start", errors)
        load_devices.assert_not_called()

    @patch("spider_cli.export_dataset")
    def test_export_failure_count_returns_one(self, export):
        """Export continues past bad files but exposes a failing final status."""
        export.return_value = ExportSummary(2, 1, 1, 2, 2, ("bad.json",))
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["export"], make_settings(Path(temp_dir), cookie=None)
            )
        self.assertEqual(code, 1)
        self.assertIn("export discovered=2 converted=1 failed=1 rows=2 columns=2", output)
        self.assertIn("bad.json", errors)

    @patch("spider_cli.collect_stats")
    def test_stats_does_not_require_cookie(self, collect):
        """Local inspection must remain usable after a Cookie expires."""
        collect.return_value = DatasetStats(2, 1, 1, 1, 100, None, None)
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output, errors = self.invoke_cli(
                ["stats"], make_settings(Path(temp_dir), cookie=None)
            )
        self.assertEqual(code, 0)
        self.assertIn("registry_devices=2", output)
        self.assertIn("data_bytes=100", output)
        self.assertEqual(errors, "")


if __name__ == "__main__":
    unittest.main()
