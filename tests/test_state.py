import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ttpbot.state import DestinationStateStore, StateStoreError


DESTINATION = "https://raceroom.z1rracing.com|z1rr"


class DestinationStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "created_races.json"
        self.store = DestinationStateStore(
            self.path, DESTINATION, "created_races", data_dir=self.root
        )

    def test_missing_save_load_and_restart_use_v2_destination_document(self):
        self.assertEqual(self.store.load(), {})
        entries = {
            "2026-08-24T20:00:00-04:00":
                "https://raceroom.z1rracing.com/z1rr/example-room"
        }
        self.store.save(entries)
        self.assertEqual(self.store.load(), entries)
        self.assertEqual(
            json.loads(self.path.read_text()),
            {
                "schema_version": 2,
                "destination_key": DESTINATION,
                "entries": entries,
            },
        )
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_schema_destination_entry_shape_and_symlinks_fail_closed(self):
        invalid = (
            {"schema_version": 1, "destination_key": DESTINATION, "entries": {}},
            {"schema_version": 2, "destination_key": "https://racetime.gg|z1rr", "entries": {}},
            {"schema_version": 2, "destination_key": DESTINATION, "entries": []},
            {"schema_version": 2, "destination_key": DESTINATION, "entries": {"key": True}},
        )
        for value in invalid:
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value))
                with self.assertRaises(StateStoreError):
                    self.store.load()
        outside = self.root.parent / "outside-state.json"
        with self.assertRaises(StateStoreError):
            DestinationStateStore(outside, DESTINATION, "created_races", data_dir=self.root)
        if hasattr(os, "symlink"):
            target = self.root / "target.json"
            target.write_text(json.dumps({
                "schema_version": 2, "destination_key": DESTINATION, "entries": {}
            }))
            link = self.root / "link.json"
            try:
                link.symlink_to(target)
            except OSError:
                pass
            else:
                with self.assertRaises(StateStoreError):
                    DestinationStateStore(
                        link, DESTINATION, "created_races", data_dir=self.root
                    ).load()

    def test_corrupt_json_is_quarantined_and_not_treated_as_empty(self):
        self.path.write_text("{not-json")
        with self.assertRaises(StateStoreError):
            self.store.load()
        self.assertFalse(self.path.exists())
        quarantined = list(self.root.glob("created_races.json.corrupt-*.bak"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(), "{not-json")

    def test_atomic_replace_failure_preserves_prior_document(self):
        original = {"2026-08-24T20:00:00-04:00": "https://raceroom.z1rracing.com/z1rr/one"}
        self.store.save(original)
        before = self.path.read_bytes()
        with patch("ttpbot.state.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(StateStoreError):
                self.store.save({
                    "2026-08-24T21:00:00-04:00": "https://raceroom.z1rracing.com/z1rr/two"
                })
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".created_races.json.*.tmp")), [])

    def test_webhook_store_requires_boolean_entries(self):
        path = self.root / "sent_webhooks.json"
        store = DestinationStateStore(path, DESTINATION, "sent_webhooks", data_dir=self.root)
        store.save({"2026-08-24T20:00:00-04:00": True})
        self.assertEqual(store.load(), {"2026-08-24T20:00:00-04:00": True})
        with self.assertRaises(StateStoreError):
            store.save({"key": "not-a-boolean"})

    def test_cleanup_removes_only_parseable_entries_before_cutoff(self):
        entries = {
            "2026-08-24T18:00:00-04:00": "https://raceroom.z1rracing.com/z1rr/old",
            "2026-08-24T22:00:00-04:00": "https://raceroom.z1rracing.com/z1rr/new",
        }
        self.store.save(entries)
        retained = self.store.cleanup_before("2026-08-24T20:00:00-04:00")
        self.assertEqual(retained, {"2026-08-24T22:00:00-04:00": entries["2026-08-24T22:00:00-04:00"]})

    def test_legacy_migration_requires_exact_assertion_and_preserves_backup(self):
        legacy = self.root / "legacy-created.json"
        legacy_entries = {
            "2026-08-24T20:00:00-04:00": "https://raceroom.z1rracing.com/z1rr/legacy"
        }
        legacy.write_text(json.dumps(legacy_entries))
        with self.assertRaises(StateStoreError):
            self.store.migrate_legacy(legacy, "https://racetime.gg|z1rr")
        migrated = self.store.migrate_legacy(legacy, DESTINATION)
        self.assertEqual(migrated, legacy_entries)
        backups = list(self.root.glob("legacy-created.json.legacy-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), legacy_entries)
        self.assertEqual(self.store.migrate_legacy(legacy, DESTINATION), legacy_entries)
        self.assertEqual(len(list(self.root.glob("legacy-created.json.legacy-*.bak"))), 1)

    def test_legacy_webhook_list_migrates_to_boolean_map(self):
        legacy = self.root / "legacy-webhooks.json"
        legacy.write_text(json.dumps(["2026-08-24T20:00:00-04:00"]))
        target = self.root / "sent_webhooks.json"
        store = DestinationStateStore(target, DESTINATION, "sent_webhooks", data_dir=self.root)
        self.assertEqual(
            store.migrate_legacy(legacy, DESTINATION),
            {"2026-08-24T20:00:00-04:00": True},
        )


if __name__ == "__main__":
    unittest.main()
