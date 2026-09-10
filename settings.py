"""Runtime configuration for the spider command."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


API_URL = "https://ztx.zcgc.cn/biz/pio-microservice-biz/pio/iot-item/his/get"


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
    """Load project paths and authentication without overriding process values."""
    project_root = (root or Path(__file__).resolve().parent).resolve()
    load_dotenv(project_root / ".env", override=False)
    return Settings(
        root=project_root,
        api_url=API_URL,
        registry_path=project_root / "data/deviceId_info_all.csv",
        raw_dir=project_root / "data/local",
        csv_dir=project_root / "data/csv",
        cookie=os.environ.get("IOT_COOKIE") or None,
    )
