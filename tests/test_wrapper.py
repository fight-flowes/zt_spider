import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT_DIR / "scripts" / "spider"


class WrapperTests(unittest.TestCase):
    def test_help_works_outside_the_project_directory(self):
        """The wrapper must resolve all paths from its own location."""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [str(WRAPPER), "help"],
                cwd=temp_dir,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("check", "crawl", "export", "stats"):
            self.assertIn(command, result.stdout)

    def test_missing_dependencies_return_two_without_traceback(self):
        """A valid interpreter without project dependencies gets a concise error."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            interpreter = root / "python-no-site"
            interpreter.write_text(
                "#!/usr/bin/env bash\n"
                f"exec {shlex.quote(sys.executable)} -S \"$@\"\n",
                encoding="utf-8",
            )
            interpreter.chmod(0o755)
            environment = os.environ.copy()
            environment["SPIDER_PYTHON"] = str(interpreter)

            result = subprocess.run(
                [str(WRAPPER), "help"],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("dependency", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
