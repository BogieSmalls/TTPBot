import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from ttpbot.preflight import perform_preflight
from ttpbot.runtime_config import resolve_bot_config
from ttpbot.state import DestinationStateStore


class FakeProviderClient:
    def __init__(self, *, category=None, server_time=None, error=None):
        self.category = category or {"slug": "z1rr", "current_races": []}
        self.server_time = server_time or datetime.now(timezone.utc)
        self.error = error
        self.calls = []

    def acquire_token(self):
        self.calls.append("token")
        if self.error:
            raise RuntimeError(self.error)
        return "opaque-token"

    def category_data(self, token):
        self.calls.append("category")
        if token != "opaque-token":
            raise AssertionError("wrong token")
        return self.category, self.server_time


def runtime_config(data_dir, *, announcements=False):
    env = {
        "TTPBOT_RACETIME_ORIGIN": "https://racetime.z1rracing.com",
        "TTPBOT_CATEGORY_SLUG": "z1rr",
        "TTPBOT_RACETIME_CLIENT_ID": "client-id",
        "TTPBOT_RACETIME_CLIENT_SECRET": "super-secret-value",
        "TTPBOT_DATA_DIR": str(data_dir),
        "TTPBOT_ENVIRONMENT": "production",
    }
    if announcements:
        env.update({
            "TTPBOT_DISCORD_WEBHOOK_URL":
                "https://discord.com/api/webhooks/12345/private-webhook-token",
            "TTPBOT_RACE_SEEKERS_ROLE_ID": "1494076623442542735",
        })
    return resolve_bot_config(argparse.Namespace(), env)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = runtime_config(self.root)

    def test_local_check_does_not_call_provider_or_mutate_state(self):
        client = FakeProviderClient(error="must not run")
        result = perform_preflight(self.config, probe=False, client=client)
        self.assertTrue(result["ok"])
        self.assertEqual(client.calls, [])
        self.assertFalse((self.root / "created_races.json").exists())
        self.assertFalse((self.root / "sent_webhooks.json").exists())
        self.assertEqual(result["destination_key"], self.config.provider.destination_key)

    def test_probe_checks_token_category_clock_collision_and_never_mutates(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        client = FakeProviderClient(server_time=now + timedelta(seconds=10))
        result = perform_preflight(
            self.config, probe=True, client=client, now=now,
            lock_probe=lambda: False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(client.calls, ["token", "category"])
        self.assertEqual(result["checks"]["clock_skew_seconds"], 10.0)
        self.assertTrue(result["checks"]["no_room_collision"])
        self.assertNotIn("super-secret-value", json.dumps(result))

    def test_clock_skew_room_collision_and_lock_each_fail(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        collision = {
            "slug": "z1rr",
            "current_races": [{
                "goal": {"name": "Beat the game"},
                "info_bot": "Triforce Triple Play | Scheduled: Mon Aug 24, 08:00 PM EDT",
            }],
        }
        cases = (
            (FakeProviderClient(server_time=now + timedelta(seconds=31)), lambda: False, "clock_within_30_seconds"),
            (FakeProviderClient(category=collision, server_time=now), lambda: False, "no_room_collision"),
            (FakeProviderClient(server_time=now), lambda: True, "scheduler_lock_available"),
        )
        for client, lock_probe, failed_check in cases:
            with self.subTest(failed_check=failed_check):
                result = perform_preflight(
                    self.config, probe=True, client=client, now=now,
                    lock_probe=lock_probe,
                )
                self.assertFalse(result["ok"])
                self.assertFalse(result["checks"][failed_check])

    def test_destination_mismatch_and_provider_failure_are_safe(self):
        wrong = DestinationStateStore(
            self.root / "created_races.json",
            "https://racetime.gg|z1rr",
            "created_races",
            data_dir=self.root,
        )
        wrong.save({})
        result = perform_preflight(self.config, probe=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["state"])
        self.assertNotIn(str(self.root), json.dumps(result))

        (self.root / "created_races.json").unlink()
        failed = perform_preflight(
            self.config,
            probe=True,
            client=FakeProviderClient(error="super-secret-value"),
            lock_probe=lambda: False,
        )
        self.assertFalse(failed["ok"])
        self.assertNotIn("super-secret-value", json.dumps(failed))

    def test_announcement_pair_is_reported_without_secret(self):
        config = runtime_config(self.root, announcements=True)
        result = perform_preflight(config, probe=False)
        self.assertTrue(result["checks"]["announcements"])
        rendered = json.dumps(result)
        self.assertNotIn("private-webhook-token", rendered)
        self.assertNotIn("1494076623442542735", rendered)


class DeploymentLockContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_service_uses_one_nonblocking_host_lock_and_hardened_directories(self):
        service = (self.root / "deploy" / "ttpbot.service").read_text()
        self.assertIn("RuntimeDirectory=ttpbot", service)
        self.assertIn("RuntimeDirectoryMode=0750", service)
        self.assertIn("StateDirectory=ttpbot", service)
        exec_lines = [line for line in service.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(len(exec_lines), 1)
        self.assertIn(
            "/usr/bin/flock --nonblock /run/ttpbot/scheduler.lock",
            exec_lines[0],
        )
        self.assertNotIn("CLIENT_SECRET", exec_lines[0])
        self.assertNotIn("WEBHOOK", exec_lines[0])
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)

    def test_operator_preflight_wrapper_is_read_only(self):
        wrapper = (self.root / "deploy" / "ttpbot-preflight").read_text()
        self.assertIn("--check-config", wrapper)
        self.assertIn("--probe", wrapper)
        self.assertNotIn("startrace", wrapper)
        self.assertNotIn("webhook", wrapper.lower())


if __name__ == "__main__":
    unittest.main()
