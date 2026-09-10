"""Local JSON export and dataset statistics."""

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from crawler import has_successful_response, load_devices


@dataclass(frozen=True)
class ExportSummary:
    discovered: int
    converted: int
    failed: int
    rows: int
    columns: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class DatasetStats:
    registry_devices: int
    registry_areas: int
    json_files: int
    csv_files: int
    data_bytes: int
    first_timestamp: str | None
    last_timestamp: str | None


def _load_series(path: Path) -> tuple[pd.DataFrame, pd.Series]:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise ValueError("API response was not successful")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("data is not an object")
    entity = data.get("entity")
    if not isinstance(entity, dict):
        raise ValueError("entity is not an object")
    points = entity.get("itemPoints", [])
    if not isinstance(points, list):
        raise ValueError("itemPoints is not a list")
    if not points:
        raise ValueError("itemPoints is empty")

    frame = pd.DataFrame(points)
    if not {"time", "value"}.issubset(frame.columns):
        raise ValueError("itemPoints requires time and value")
    frame = frame[["time", "value"]].copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = (
        frame.dropna(subset=["time"])
        .drop_duplicates(subset=["time"], keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise ValueError("itemPoints contains no valid timestamps")
    series = frame.set_index("time")["value"].rename(f"{path.stem}_value")
    return frame, series


def export_dataset(raw_dir: Path, csv_dir: Path) -> ExportSummary:
    """Convert successful raw responses and build one merged wide CSV."""
    json_files = sorted(raw_dir.glob("*/*.json")) if raw_dir.exists() else []
    series_list = []
    device_rows = []
    errors = []
    for path in json_files:
        try:
            frame, series = _load_series(path)
            output_dir = csv_dir / path.parent.name
            output_dir.mkdir(parents=True, exist_ok=True)
            frame.to_csv(
                output_dir / f"{path.stem}.csv",
                index=False,
                encoding="utf-8-sig",
                date_format="%Y-%m-%d %H:%M:%S",
            )
            series_list.append(series)
            device_rows.append(
                {"deviceId": path.stem, "service_area": path.parent.name}
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            errors.append(f"{path.parent.name}/{path.name}: {error}")

    rows = columns = 0
    if series_list:
        csv_dir.mkdir(parents=True, exist_ok=True)
        merged = pd.concat(series_list, axis=1, join="outer").sort_index()
        merged.index.name = "time"
        merged = merged.reset_index()
        merged.to_csv(
            csv_dir / "merged_power_data.csv",
            index=False,
            encoding="utf-8-sig",
            date_format="%Y-%m-%d %H:%M:%S",
        )
        mapping = pd.DataFrame(
            device_rows, columns=["deviceId", "service_area"]
        ).sort_values(["service_area", "deviceId"])
        mapping.to_csv(
            csv_dir / "deviceId_info.csv", index=False, encoding="utf-8-sig"
        )
        rows, columns = merged.shape

    return ExportSummary(
        discovered=len(json_files),
        converted=len(series_list),
        failed=len(errors),
        rows=rows,
        columns=columns,
        errors=tuple(errors),
    )


def _directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def collect_stats(
    registry_path: Path, raw_dir: Path, csv_dir: Path
) -> DatasetStats:
    """Summarize registry coverage and generated artifacts."""
    devices = load_devices(registry_path)
    successful_json = [
        path
        for path in raw_dir.glob("*/*.json")
        if has_successful_response(path)
    ] if raw_dir.exists() else []
    device_csv = list(csv_dir.glob("*/*.csv")) if csv_dir.exists() else []

    first_timestamp = last_timestamp = None
    merged_path = csv_dir / "merged_power_data.csv"
    if merged_path.is_file():
        times = pd.to_datetime(
            pd.read_csv(merged_path, usecols=["time"])["time"], errors="coerce"
        ).dropna()
        if not times.empty:
            first_timestamp = times.min().strftime("%Y-%m-%d %H:%M:%S")
            last_timestamp = times.max().strftime("%Y-%m-%d %H:%M:%S")

    return DatasetStats(
        registry_devices=len({device.device_id for device in devices}),
        registry_areas=len({device.service_area for device in devices}),
        json_files=len(successful_json),
        csv_files=len(device_csv),
        data_bytes=_directory_bytes(raw_dir) + _directory_bytes(csv_dir),
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
    )
