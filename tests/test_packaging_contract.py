import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackagingContractTests(unittest.TestCase):
    def test_windows_timezone_fallback_is_declared_and_locked(self):
        setup_text = (ROOT / "setup.py").read_text(encoding="utf-8")
        lock_lines = {
            line.strip()
            for line in (ROOT / "requirements.lock").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.startswith("#")
        }

        self.assertIn(
            "tzdata>=2026.3; platform_system==\"Windows\"",
            setup_text,
        )
        self.assertIn("tzdata==2026.3", lock_lines)

    def test_module_entrypoint_propagates_main_return_code(self):
        env = os.environ.copy()
        env.update(
            {
                "TTPBOT_RACETIME_CLIENT_ID": "client-id",
                "TTPBOT_RACETIME_CLIENT_SECRET": "client-secret",
                "TTPBOT_DATA_DIR": str(ROOT),
            }
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "ttpbot",
                "--origin",
                "http://127.0.0.1:9",
                "--category",
                "z1rr",
                "--environment",
                "test",
                "--allow-insecure-loopback",
                "--probe",
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('"ok":false', completed.stdout)


if __name__ == "__main__":
    unittest.main()
