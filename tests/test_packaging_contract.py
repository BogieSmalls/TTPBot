from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
