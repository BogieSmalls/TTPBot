from pathlib import Path
import unittest


class DeploymentDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.env = (cls.root / "deploy" / "ttpbot.env.example").read_text()
        cls.readme = (cls.root / "README.md").read_text()
        cls.runbook = (cls.root / "docs" / "oci-service-runbook.md").read_text()
        cls.all_text = "\n".join((cls.env, cls.readme, cls.runbook))

    def test_env_example_has_exact_safe_runtime_contract(self):
        expected = {
            "TTPBOT_RACETIME_ORIGIN",
            "TTPBOT_CATEGORY_SLUG",
            "TTPBOT_RACETIME_CLIENT_ID",
            "TTPBOT_RACETIME_CLIENT_SECRET",
            "TTPBOT_DISCORD_WEBHOOK_URL",
            "TTPBOT_RACE_SEEKERS_ROLE_ID",
            "TTPBOT_DATA_DIR",
            "TTPBOT_ENVIRONMENT",
        }
        variables = {
            line.split("=", 1)[0]
            for line in self.env.splitlines()
            if line and not line.startswith("#") and "=" in line
        }
        self.assertEqual(variables, expected)
        for secret in (
            "TTPBOT_RACETIME_CLIENT_ID",
            "TTPBOT_RACETIME_CLIENT_SECRET",
            "TTPBOT_DISCORD_WEBHOOK_URL",
            "TTPBOT_RACE_SEEKERS_ROLE_ID",
        ):
            line = next(line for line in self.env.splitlines() if line.startswith(secret + "="))
            self.assertEqual(line, secret + "=")
        self.assertNotIn("TTPBOT_Z1R_WEBHOOK_URL", self.all_text)

    def test_both_outcomes_and_preflight_are_documented(self):
        for value in (
            "https://racetime.gg",
            "https://raceroom.z1rracing.com",
            "TTPBOT_CATEGORY_SLUG=z1rr",
            "--check-config",
            "--probe",
            "destination_key",
        ):
            self.assertIn(value, self.all_text)
        self.assertIn("root:ttpbot", self.runbook)
        self.assertIn("0640", self.runbook)
        self.assertIn("requirements.lock", self.runbook)

    def test_cutover_and_rollback_are_fail_closed_and_single_scheduler(self):
        cutover_terms = (
            "ROOM_OPEN_MINUTES_BEFORE + 10",
            "stop and disable",
            "verify the process and scheduler lock are absent",
            "back up",
            "migrate",
            "atomically",
            "--check-config",
            "--probe",
            "exactly one scheduler lock",
            "observe the next room",
            "do not delete",
        )
        for term in cutover_terms:
            self.assertIn(term.lower(), self.runbook.lower())
        rollback_terms = (
            "stop the new scheduler first",
            "restore the prior release, environment, and state as one set",
            "probe the old destination",
            "counterpart room",
            "never run both schedulers",
        )
        for term in rollback_terms:
            self.assertIn(term.lower(), self.runbook.lower())

    def test_docs_require_secret_redaction_and_first_room_acceptance(self):
        for term in (
            "never print",
            "room URL",
            "one Discord announcement",
            "created_races.json",
            "sent_webhooks.json",
        ):
            self.assertIn(term, self.all_text)


if __name__ == "__main__":
    unittest.main()
