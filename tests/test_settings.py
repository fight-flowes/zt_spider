import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from settings import load_settings


class SettingsTests(unittest.TestCase):
    def test_process_cookie_overrides_dotenv_cookie(self):
        """A deployment Cookie must not be replaced by the local .env file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("IOT_COOKIE=file-cookie\n", encoding="utf-8")

            with patch.dict(os.environ, {"IOT_COOKIE": "process-cookie"}, clear=True):
                settings = load_settings(root)

        self.assertEqual(settings.cookie, "process-cookie")

    def test_paths_are_rooted_at_project(self):
        """Commands must work independently of the caller's working directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(root)

        self.assertEqual(settings.registry_path, root / "data/deviceId_info_all.csv")
        self.assertEqual(settings.raw_dir, root / "data/local")
        self.assertEqual(settings.csv_dir, root / "data/csv")
        self.assertIsNone(settings.cookie)


if __name__ == "__main__":
    unittest.main()
