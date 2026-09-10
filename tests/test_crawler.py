import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import requests


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from crawler import (
    AuthenticationError,
    CrawlSummary,
    Device,
    IoTClient,
    SpiderError,
    crawl_devices,
    load_devices,
    select_devices,
)
from settings import Settings


class DeviceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.devices = [
            Device("10001", "测试服务区"),
            Device("10002", "另一服务区"),
            Device("10003", "测试服务区"),
        ]

    def test_load_devices_accepts_utf8_and_gbk(self):
        """Both supported registry encodings must yield the same device mapping."""
        content = "deviceId,service_area\n10001,测试服务区\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            utf8_path = root / "utf8.csv"
            gbk_path = root / "gbk.csv"
            utf8_path.write_text(content, encoding="utf-8-sig")
            gbk_path.write_text(content, encoding="gbk")

            utf8_devices = load_devices(utf8_path)
            gbk_devices = load_devices(gbk_path)

        expected = [Device("10001", "测试服务区")]
        self.assertEqual(utf8_devices, expected)
        self.assertEqual(gbk_devices, expected)

    def test_select_devices_requires_exactly_one_selector(self):
        """Missing or mixed selectors must fail before any crawl can start."""
        with self.assertRaisesRegex(ValueError, "exactly one"):
            select_devices(self.devices)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            select_devices(self.devices, all_devices=True, areas=["测试服务区"])

    def test_select_devices_preserves_registry_order_and_area(self):
        """ID selection must infer areas and retain deterministic registry ordering."""
        selected = select_devices(self.devices, device_ids=["10003", "10001"])
        self.assertEqual(
            selected,
            [Device("10001", "测试服务区"), Device("10003", "测试服务区")],
        )

    def test_select_devices_rejects_unknown_values(self):
        """A typo in a selector must not silently produce an incomplete crawl."""
        with self.assertRaisesRegex(ValueError, "未知服务区"):
            select_devices(self.devices, areas=["不存在"])
        with self.assertRaisesRegex(ValueError, "99999"):
            select_devices(self.devices, device_ids=["99999"])


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"code": 200, "data": {}}
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, response=None, request_error=None):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.response = response or FakeResponse()
        self.request_error = request_error
        self.last_url = None
        self.last_params = None
        self.last_timeout = None

    def get(self, url, params, timeout):
        self.last_url = url
        self.last_params = params
        self.last_timeout = timeout
        if self.request_error:
            raise self.request_error
        return self.response


def make_settings(root: Path, cookie="SESSION=session-value; access_token=token-value"):
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


class IoTClientTests(unittest.TestCase):
    def test_client_sets_cookie_pairs_and_request_contract(self):
        """The real API boundary must receive authentication and fixed power parameters."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = FakeSession(FakeResponse(payload={"code": 200, "data": {"entity": {}}}))
            client = IoTClient(make_settings(Path(temp_dir)), session=session)

            result = client.fetch_history("10001", date(2026, 1, 1), date(2026, 1, 2))

        self.assertEqual(result, {"code": 200, "data": {"entity": {}}})
        self.assertEqual(session.cookies.get("SESSION"), "session-value")
        self.assertEqual(session.cookies.get("access_token"), "token-value")
        self.assertEqual(session.last_url, "https://example.test/history")
        self.assertEqual(
            session.last_params,
            {
                "deviceId": "10001",
                "startTime": "2026-01-01 00:00:00",
                "endTime": "2026-01-02 23:59:59",
                "periodType": 10,
                "itemTag": "P",
            },
        )
        self.assertEqual(session.last_timeout, 17)

    def test_client_rejects_missing_or_malformed_cookie(self):
        """An unauthenticated client must fail locally before creating requests."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "IOT_COOKIE"):
                IoTClient(make_settings(root, cookie=None), session=FakeSession())
            with self.assertRaisesRegex(ValueError, "cookie pairs"):
                IoTClient(make_settings(root, cookie="not-a-cookie"), session=FakeSession())

    def test_client_maps_unauthorized_response(self):
        """HTTP 401 must produce an actionable authentication error."""
        with tempfile.TemporaryDirectory() as temp_dir:
            client = IoTClient(
                make_settings(Path(temp_dir)),
                session=FakeSession(FakeResponse(status_code=401)),
            )
            with self.assertRaisesRegex(AuthenticationError, "Cookie"):
                client.fetch_history("10001", date(2026, 1, 1), date(2026, 1, 1))

    def test_client_hides_cookie_when_network_fails(self):
        """Operational errors must not leak secret Cookie values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            client = IoTClient(
                make_settings(Path(temp_dir)),
                session=FakeSession(request_error=requests.ConnectionError("offline")),
            )
            with self.assertRaises(SpiderError) as raised:
                client.fetch_history("10001", date(2026, 1, 1), date(2026, 1, 1))

        message = str(raised.exception)
        self.assertIn("offline", message)
        self.assertNotIn("session-value", message)
        self.assertNotIn("token-value", message)

    def test_client_rejects_invalid_json_and_api_error(self):
        """Malformed and unsuccessful API payloads must not be treated as data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = IoTClient(
                make_settings(root),
                session=FakeSession(FakeResponse(json_error=ValueError("bad json"))),
            )
            with self.assertRaisesRegex(SpiderError, "JSON"):
                invalid.fetch_history("10001", date(2026, 1, 1), date(2026, 1, 1))

            rejected = IoTClient(
                make_settings(root),
                session=FakeSession(FakeResponse(payload={"code": 500, "message": "rejected"})),
            )
            with self.assertRaisesRegex(SpiderError, "API code 500"):
                rejected.fetch_history("10001", date(2026, 1, 1), date(2026, 1, 1))


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response or {"code": 200, "data": {"entity": {"itemPoints": []}}}
        self.error = error
        self.calls = []

    def fetch_history(self, device_id, start, end):
        self.calls.append((device_id, start, end))
        if self.error:
            raise self.error
        return self.response


class CrawlDevicesTests(unittest.TestCase):
    def setUp(self):
        self.device = Device("10001", "测试服务区")
        self.start = date(2026, 1, 1)
        self.end = date(2026, 1, 2)

    def test_valid_existing_json_is_skipped(self):
        """Resume mode must not call the API for a completed device."""
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            destination = raw_dir / self.device.service_area / "10001.json"
            destination.parent.mkdir()
            destination.write_text('{"code": 200}', encoding="utf-8")
            client = FakeClient()

            summary = crawl_devices(
                client, [self.device], raw_dir, self.start, self.end, delay=0
            )

        self.assertEqual(summary, CrawlSummary(1, 0, 1, 0, ()))
        self.assertEqual(client.calls, [])

    def test_force_atomically_replaces_existing_json(self):
        """A forced successful refresh must replace the target and leave no temp file."""
        response = {"code": 200, "data": {"entity": {"itemPoints": [{"time": "x", "value": "1"}]}}}
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            destination = raw_dir / self.device.service_area / "10001.json"
            destination.parent.mkdir()
            destination.write_text('{"code": 200, "old": true}', encoding="utf-8")

            summary = crawl_devices(
                FakeClient(response=response),
                [self.device],
                raw_dir,
                self.start,
                self.end,
                force=True,
                delay=0,
            )
            saved = json.loads(destination.read_text(encoding="utf-8"))
            temp_files = list(destination.parent.glob("*.tmp"))

        self.assertEqual(summary.downloaded, 1)
        self.assertEqual(saved, response)
        self.assertEqual(temp_files, [])

    def test_failed_force_keeps_existing_json_and_continues(self):
        """A failed refresh must preserve prior data and be represented in the summary."""
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            destination = raw_dir / self.device.service_area / "10001.json"
            destination.parent.mkdir()
            destination.write_text('{"code": 200, "old": true}', encoding="utf-8")
            before = destination.read_bytes()

            summary = crawl_devices(
                FakeClient(error=SpiderError("offline")),
                [self.device, Device("10002", "测试服务区")],
                raw_dir,
                self.start,
                self.end,
                force=True,
                delay=0,
            )
            after = destination.read_bytes()

        self.assertEqual(after, before)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.failed, 2)
        self.assertEqual(len(summary.errors), 2)

    def test_progress_reports_each_processed_device_and_current_counts(self):
        """Progress must include skipped, downloaded, and failed devices."""
        class SequencedClient:
            def __init__(self):
                self.outcomes = iter(
                    [
                        {"code": 200, "data": {"entity": {"itemPoints": []}}},
                        SpiderError("offline"),
                    ]
                )

            def fetch_history(self, device_id, start, end):
                outcome = next(self.outcomes)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        devices = [
            self.device,
            Device("10002", "测试服务区"),
            Device("10003", "测试服务区"),
        ]
        updates = []
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            existing = raw_dir / self.device.service_area / "10001.json"
            existing.parent.mkdir()
            existing.write_text('{"code": 200}', encoding="utf-8")

            try:
                crawl_devices(
                    SequencedClient(),
                    devices,
                    raw_dir,
                    self.start,
                    self.end,
                    delay=0,
                    progress=lambda *values: updates.append(values),
                )
            except TypeError as error:
                self.fail(f"progress callback is not supported: {error}")

        self.assertEqual(
            updates,
            [
                (1, 3, 0, 1, 0),
                (2, 3, 1, 1, 0),
                (3, 3, 1, 1, 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
