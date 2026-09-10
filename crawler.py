"""Device selection and remote history acquisition."""

import csv
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from settings import Settings


class SpiderError(RuntimeError):
    """A safe-to-display operational crawler error."""


class AuthenticationError(SpiderError):
    """The remote service rejected the configured Cookie."""


@dataclass(frozen=True)
class Device:
    device_id: str
    service_area: str


@dataclass(frozen=True)
class CrawlSummary:
    total: int
    downloaded: int
    skipped: int
    failed: int
    errors: tuple[str, ...]


def load_devices(path: Path) -> list[Device]:
    """Load a UTF-8 or GBK device registry."""
    decode_error = None
    for encoding in ("utf-8-sig", "gbk"):
        try:
            with path.open(encoding=encoding, newline="") as source:
                reader = csv.DictReader(source)
                required = {"deviceId", "service_area"}
                if not reader.fieldnames or not required.issubset(reader.fieldnames):
                    raise ValueError("device registry requires deviceId and service_area columns")
                return [
                    Device(row["deviceId"].strip(), row["service_area"].strip())
                    for row in reader
                    if row.get("deviceId", "").strip()
                    and row.get("service_area", "").strip()
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
    """Select targets with exactly one explicit selector kind."""
    selectors = int(all_devices) + int(bool(areas)) + int(bool(device_ids))
    if selectors != 1:
        raise ValueError("choose exactly one of --all, --area, or --device")
    if all_devices:
        return list(devices)

    if areas:
        requested = set(areas)
        known = {device.service_area for device in devices}
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(f"未知服务区: {', '.join(unknown)}")
        return [device for device in devices if device.service_area in requested]

    requested_ids = set(device_ids or [])
    known_ids = {device.device_id for device in devices}
    unknown_ids = sorted(requested_ids - known_ids)
    if unknown_ids:
        raise ValueError(f"未知设备ID: {', '.join(unknown_ids)}")
    return [device for device in devices if device.device_id in requested_ids]


def parse_cookie_header(cookie: str) -> dict[str, str]:
    """Parse a browser Cookie request header without interpreting its values."""
    pairs = {}
    for item in cookie.split(";"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if name:
            pairs[name] = value.strip()
    return pairs


class IoTClient:
    """Authenticated client for one remote history endpoint."""

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        if not settings.cookie:
            raise ValueError("IOT_COOKIE is not configured")
        cookies = parse_cookie_header(settings.cookie)
        if not cookies:
            raise ValueError("IOT_COOKIE contains no valid cookie pairs")

        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://ztx.zcgc.cn/static/energy/consumption/MbmMonitor.html",
            }
        )
        self.session.cookies.update(cookies)

    def fetch_history(self, device_id: str, start: date, end: date) -> dict:
        """Fetch one device history response for an inclusive date range."""
        params = {
            "deviceId": device_id,
            "startTime": f"{start.isoformat()} 00:00:00",
            "endTime": f"{end.isoformat()} 23:59:59",
            "periodType": 10,
            "itemTag": "P",
        }
        try:
            response = self.session.get(
                self.settings.api_url,
                params=params,
                timeout=self.settings.timeout,
            )
        except requests.RequestException as error:
            raise SpiderError(f"request failed: {error}") from error

        if response.status_code == 401:
            raise AuthenticationError("Cookie was rejected or has expired")
        try:
            response.raise_for_status()
        except requests.RequestException as error:
            raise SpiderError(f"HTTP request failed: {response.status_code}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise SpiderError("response is not valid JSON") from error
        if not isinstance(payload, dict):
            raise SpiderError("response JSON is not an object")
        if payload.get("code") != 200:
            raise SpiderError(f"API code {payload.get('code', 'missing')}")
        return payload


def has_successful_response(path: Path) -> bool:
    """Return whether a destination already contains a successful API response."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with path.open(encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("code") == 200


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise SpiderError(f"cannot save {path.name}: {error}") from error


def crawl_devices(
    client: IoTClient,
    devices: list[Device],
    raw_dir: Path,
    start: date,
    end: date,
    *,
    force: bool = False,
    delay: float = 0.2,
    progress: Callable[[int, int, int, int, int], None] | None = None,
) -> CrawlSummary:
    """Download selected devices while preserving safe resume behavior."""
    downloaded = skipped = failed = 0
    errors = []
    total = len(devices)
    for completed, device in enumerate(devices, start=1):
        destination = raw_dir / device.service_area / f"{device.device_id}.json"
        if not force and has_successful_response(destination):
            skipped += 1
            requested = False
        else:
            requested = True
            try:
                payload = client.fetch_history(device.device_id, start, end)
                _write_json_atomic(destination, payload)
                downloaded += 1
            except SpiderError as error:
                failed += 1
                errors.append(f"{device.service_area}/{device.device_id}: {error}")
        if progress is not None:
            progress(completed, total, downloaded, skipped, failed)
        if requested and delay > 0:
            time.sleep(delay)
    return CrawlSummary(total, downloaded, skipped, failed, tuple(errors))
