#!/usr/bin/env python3
"""Non-interactive command-line interface for the IoT data spider."""

import argparse
import sys
from collections.abc import Sequence
from datetime import date

try:
    from crawler import (
        IoTClient,
        SpiderError,
        crawl_devices,
        load_devices,
        select_devices,
    )
    from dataset import collect_stats, export_dataset
    from settings import Settings, load_settings
except ModuleNotFoundError as error:
    missing = error.name or "unknown"
    print(
        f"error: missing Python dependency: {missing}; "
        "run python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2) from None


def iso_date(value: str) -> date:
    """Parse an ISO date for argparse with a concise user-facing error."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from error


def build_parser() -> argparse.ArgumentParser:
    """Build the public command tree."""
    parser = argparse.ArgumentParser(
        prog="scripts/spider",
        description="Crawl and export IoT device history data.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("help", help="show this help message")

    check = subcommands.add_parser("check", help="test Cookie and API access")
    check.add_argument("--device", help="device ID to probe (default: first device)")

    crawl = subcommands.add_parser("crawl", help="download device history")
    selectors = crawl.add_mutually_exclusive_group(required=True)
    selectors.add_argument(
        "--all", action="store_true", dest="all_devices", help="select all devices"
    )
    selectors.add_argument(
        "--area", action="append", dest="areas", metavar="NAME", help="select an area"
    )
    selectors.add_argument(
        "--device",
        action="append",
        dest="device_ids",
        metavar="ID",
        help="select a device",
    )
    crawl.add_argument("--start", type=iso_date, default=date(2022, 1, 1))
    crawl.add_argument("--end", type=iso_date, default=date.today())
    crawl.add_argument(
        "--force", action="store_true", help="replace existing successful JSON"
    )

    subcommands.add_parser("export", help="convert JSON and build merged CSV")
    subcommands.add_parser("stats", help="show local data statistics")
    return parser


class _ProgressReporter:
    """Render crawl progress appropriately for terminals and redirected logs."""

    def __init__(self, stream):
        self.stream = stream
        self.is_terminal = stream.isatty()

    def __call__(
        self,
        completed: int,
        total: int,
        downloaded: int,
        skipped: int,
        failed: int,
    ) -> None:
        percentage = round(completed / total * 100) if total else 100
        line = (
            f"[{completed}/{total}] {percentage}% downloaded={downloaded} "
            f"skipped={skipped} failed={failed}"
        )
        if self.is_terminal:
            end = "\n" if completed == total else ""
            print(f"\r{line}", end=end, file=self.stream, flush=True)
        else:
            print(line, file=self.stream, flush=True)


def _check(args: argparse.Namespace, settings: Settings) -> int:
    client = IoTClient(settings)
    devices = load_devices(settings.registry_path)
    if not devices:
        raise ValueError("device registry is empty")
    target = (
        select_devices(devices, device_ids=[args.device])[0]
        if args.device
        else devices[0]
    )
    today = date.today()
    client.fetch_history(target.device_id, today, today)
    print(f"check ok device={target.device_id} area={target.service_area}")
    return 0


def _crawl(args: argparse.Namespace, settings: Settings) -> int:
    if args.start > args.end:
        raise ValueError("--start must not be later than --end")
    devices = load_devices(settings.registry_path)
    selected = select_devices(
        devices,
        all_devices=args.all_devices,
        areas=args.areas,
        device_ids=args.device_ids,
    )
    client = IoTClient(settings)
    summary = crawl_devices(
        client,
        selected,
        settings.raw_dir,
        args.start,
        args.end,
        force=args.force,
        delay=settings.request_delay,
        progress=_ProgressReporter(sys.stdout),
    )
    print(
        f"crawl total={summary.total} downloaded={summary.downloaded} "
        f"skipped={summary.skipped} failed={summary.failed}"
    )
    for error in summary.errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if summary.failed else 0


def _export(settings: Settings) -> int:
    summary = export_dataset(settings.raw_dir, settings.csv_dir)
    print(
        f"export discovered={summary.discovered} converted={summary.converted} "
        f"failed={summary.failed} rows={summary.rows} columns={summary.columns}"
    )
    for error in summary.errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if summary.failed else 0


def _stats(settings: Settings) -> int:
    stats = collect_stats(
        settings.registry_path, settings.raw_dir, settings.csv_dir
    )
    first = stats.first_timestamp or "none"
    last = stats.last_timestamp or "none"
    print(
        f"stats registry_devices={stats.registry_devices} "
        f"registry_areas={stats.registry_areas} json_files={stats.json_files} "
        f"csv_files={stats.csv_files} data_bytes={stats.data_bytes} "
        f"first={first} last={last}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one command and return its process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "help":
        parser.print_help()
        return 0

    try:
        settings = load_settings()
        if args.command == "check":
            return _check(args, settings)
        if args.command == "crawl":
            return _crawl(args, settings)
        if args.command == "export":
            return _export(settings)
        if args.command == "stats":
            return _stats(settings)
    except SpiderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except (FileNotFoundError, PermissionError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
