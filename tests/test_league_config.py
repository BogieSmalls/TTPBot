import argparse
import unittest

from ttpbot.runtime_config import RuntimeConfigurationError, resolve_bot_config

WEBHOOK = 'https://discord.com/api/webhooks/123/abcdef'
BASE = {
    'TTPBOT_RACETIME_ORIGIN': 'https://racetime.gg',
    'TTPBOT_CATEGORY_SLUG': 'z1r',
    'TTPBOT_RACETIME_CLIENT_ID': 'id',
    'TTPBOT_RACETIME_CLIENT_SECRET': 'secret',
    'TTPBOT_DATA_DIR': '/var/lib/ttpbot',
    'TTPBOT_ENVIRONMENT': 'production',
}


def config(**overrides):
    env = dict(BASE)
    env.update(overrides)
    return resolve_bot_config(argparse.Namespace(), env=env)


class LeagueConfigTests(unittest.TestCase):
    def test_league_is_disabled_by_default(self):
        self.assertFalse(config().league_enabled)

    def test_league_can_be_enabled(self):
        cfg = config(TTPBOT_LEAGUE_ENABLED='true',
                     TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL=WEBHOOK)
        self.assertTrue(cfg.league_enabled)

    def test_schedule_url_has_a_default(self):
        self.assertIn('docs.google.com', config().league_schedule_url)

    def test_schedule_url_can_be_overridden(self):
        cfg = config(TTPBOT_LEAGUE_SCHEDULE_URL='https://example.com/s.csv')
        self.assertEqual(cfg.league_schedule_url, 'https://example.com/s.csv')

    def test_league_webhook_is_validated(self):
        with self.assertRaises(RuntimeConfigurationError):
            config(TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL='https://evil.example/hook')

    def test_league_webhook_is_independent_of_the_ttp_webhook(self):
        # TTP's webhook+role pairing rule must not apply to League.
        cfg = config(TTPBOT_LEAGUE_ENABLED='true',
                     TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL=WEBHOOK)
        self.assertIsNone(cfg.discord_webhook_url)
        self.assertEqual(cfg.league_discord_webhook_url, WEBHOOK)

    def test_enabled_without_a_webhook_is_allowed(self):
        # Rooms and invites still run; only the announcement is skipped.
        cfg = config(TTPBOT_LEAGUE_ENABLED='true')
        self.assertTrue(cfg.league_enabled)
        self.assertIsNone(cfg.league_discord_webhook_url)

    def test_invalid_enabled_value_is_rejected(self):
        with self.assertRaises(RuntimeConfigurationError):
            config(TTPBOT_LEAGUE_ENABLED='sometimes')

    def test_ttp_config_is_unchanged_when_league_is_off(self):
        cfg = config()
        self.assertEqual(cfg.category_slug, 'z1r')
        self.assertEqual(cfg.environment, 'production')


if __name__ == '__main__':
    unittest.main()
