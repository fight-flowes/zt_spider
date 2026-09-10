import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from dataset import collect_stats, export_dataset


def write_response(path: Path, points, code=200):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"code": code, "data": {"entity": {"itemPoints": points}}}),
        encoding="utf-8",
    )


class DatasetExportTests(unittest.TestCase):
    def test_export_writes_device_csv_merged_csv_and_mapping(self):
        """Valid raw responses must produce all three documented export forms."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "local"
            csv_dir = root / "csv"
            write_response(
                raw_dir / "A服务区" / "10001.json",
                [
                    {"time": "2026-01-01 00:00:00", "value": "1.0"},
                    {"time": "2026-01-01 00:15:00", "value": "2.0"},
                ],
            )
            write_response(
                raw_dir / "B服务区" / "10002.json",
                [
                    {"time": "2026-01-01 00:15:00", "value": "3.0"},
                    {"time": "2026-01-01 00:30:00", "value": "4.0"},
                ],
            )

            summary = export_dataset(raw_dir, csv_dir)
            first = pd.read_csv(csv_dir / "A服务区" / "10001.csv")
            merged = pd.read_csv(csv_dir / "merged_power_data.csv")
            mapping = pd.read_csv(csv_dir / "deviceId_info.csv", dtype=str)

        self.assertEqual(summary.discovered, 2)
        self.assertEqual(summary.converted, 2)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(summary.rows, 3)
        self.assertEqual(summary.columns, 3)
        self.assertEqual(list(first.columns), ["time", "value"])
        self.assertEqual(
            list(merged.columns), ["time", "10001_value", "10002_value"]
        )
        self.assertEqual(mapping.to_dict("records"), [
            {"deviceId": "10001", "service_area": "A服务区"},
            {"deviceId": "10002", "service_area": "B服务区"},
        ])

    def test_export_reports_malformed_and_empty_responses(self):
        """Bad files must be counted without preventing valid files from exporting."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "local"
            csv_dir = root / "csv"
            write_response(
                raw_dir / "A服务区" / "10001.json",
                [{"time": "2026-01-01 00:00:00", "value": "1"}],
            )
            malformed = raw_dir / "A服务区" / "10002.json"
            malformed.write_text("not-json", encoding="utf-8")
            write_response(raw_dir / "A服务区" / "10003.json", [])

            summary = export_dataset(raw_dir, csv_dir)

        self.assertEqual(summary.discovered, 3)
        self.assertEqual(summary.converted, 1)
        self.assertEqual(summary.failed, 2)
        self.assertEqual(len(summary.errors), 2)
        self.assertTrue(any("10002.json" in error for error in summary.errors))
        self.assertTrue(any("10003.json" in error for error in summary.errors))

    def test_export_isolates_structurally_malformed_responses(self):
        """A malformed payload shape must not prevent unrelated files from exporting."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "local"
            csv_dir = root / "csv"
            write_response(
                raw_dir / "A服务区" / "10001.json",
                [{"time": "2026-01-01 00:00:00", "value": "1"}],
            )
            malformed = raw_dir / "A服务区" / "10002.json"
            malformed.write_text(
                json.dumps({"code": 200, "data": None}), encoding="utf-8"
            )

            summary = export_dataset(raw_dir, csv_dir)

        self.assertEqual(summary.discovered, 2)
        self.assertEqual(summary.converted, 1)
        self.assertEqual(summary.failed, 1)
        self.assertIn("10002.json", summary.errors[0])


class DatasetStatsTests(unittest.TestCase):
    def test_collect_stats_reports_counts_size_and_merged_coverage(self):
        """Stats must distinguish successful device artifacts from aggregate CSVs."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = root / "devices.csv"
            raw_dir = root / "local"
            csv_dir = root / "csv"
            registry.write_text(
                "deviceId,service_area\n10001,A服务区\n10002,A服务区\n",
                encoding="utf-8",
            )
            write_response(raw_dir / "A服务区" / "10001.json", [])
            write_response(raw_dir / "A服务区" / "10002.json", [], code=500)
            device_csv = csv_dir / "A服务区" / "10001.csv"
            device_csv.parent.mkdir(parents=True)
            device_csv.write_text("time,value\n2026-01-01 00:00:00,1\n", encoding="utf-8")
            csv_dir.joinpath("merged_power_data.csv").write_text(
                "time,10001_value\n"
                "2026-01-01 00:00:00,1\n"
                "2026-01-01 00:15:00,2\n",
                encoding="utf-8",
            )
            csv_dir.joinpath("deviceId_info.csv").write_text(
                "deviceId,service_area\n10001,A服务区\n", encoding="utf-8"
            )

            stats = collect_stats(registry, raw_dir, csv_dir)

        self.assertEqual(stats.registry_devices, 2)
        self.assertEqual(stats.registry_areas, 1)
        self.assertEqual(stats.json_files, 1)
        self.assertEqual(stats.csv_files, 1)
        self.assertGreater(stats.data_bytes, 0)
        self.assertEqual(stats.first_timestamp, "2026-01-01 00:00:00")
        self.assertEqual(stats.last_timestamp, "2026-01-01 00:15:00")


if __name__ == "__main__":
    unittest.main()
