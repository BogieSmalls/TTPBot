from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from ttpbot import migrate_state


class LegacyMigrationCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy = self.root / "legacy"
        self.target = self.root / "target"
        self.legacy.mkdir()
        self.target.mkdir()
        (self.legacy / "created_races.json").write_text(json.dumps({
            "2026-08-24T20:00:00-04:00": "https://racetime.gg/z1rr/legacy-room"
        }))
        (self.legacy / "sent_webhooks.json").write_text(json.dumps([
            "2026-08-24T20:00:00-04:00"
        ]))

    def test_migrates_both_documents_to_asserted_destination_and_is_idempotent(self):
        result = migrate_state.migrate_legacy_state(
            self.legacy,
            self.target,
            origin="https://racetime.gg",
            category="z1rr",
        )
        self.assertEqual(result, {"created_races": 1, "sent_webhooks": 1})
        for name in ("created_races.json", "sent_webhooks.json"):
            value = json.loads((self.target / name).read_text())
            self.assertEqual(value["schema_version"], 2)
            self.assertEqual(value["destination_key"], "https://racetime.gg|z1rr")
        second = migrate_state.migrate_legacy_state(
            self.legacy,
            self.target,
            origin="https://racetime.gg",
            category="z1rr",
        )
        self.assertEqual(second, result)
        self.assertEqual(len(list(self.legacy.glob("*.legacy-*.bak"))), 2)

    def test_missing_or_overlapping_directories_fail(self):
        (self.legacy / "sent_webhooks.json").unlink()
        with self.assertRaises(migrate_state.MigrationError):
            migrate_state.migrate_legacy_state(
                self.legacy, self.target,
                origin="https://racetime.gg", category="z1rr",
            )
        with self.assertRaises(migrate_state.MigrationError):
            migrate_state.migrate_legacy_state(
                self.legacy, self.legacy,
                origin="https://racetime.gg", category="z1rr",
            )

    def test_cli_prints_only_safe_destination_and_counts(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = migrate_state.main([
                "--legacy-dir", str(self.legacy),
                "--data-dir", str(self.target),
                "--origin", "https://racetime.gg",
                "--category", "z1rr",
            ])
        self.assertEqual(result, 0)
        rendered = stdout.getvalue().strip()
        self.assertEqual(
            rendered,
            "TTPBOT_STATE_MIGRATION=PASS destination=https://racetime.gg|z1rr "
            "created_races=1 sent_webhooks=1",
        )
        self.assertNotIn(str(self.root), rendered)


if __name__ == "__main__":
    unittest.main()
