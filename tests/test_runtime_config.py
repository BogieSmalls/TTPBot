import argparse
import unittest

from ttpbot.runtime_config import (
    RuntimeConfigurationError,
    missing_config_names,
    resolve_bot_config,
)


def args(category_slug=None, client_id=None, client_secret=None):
    return argparse.Namespace(
        category_slug=category_slug,
        client_id=client_id,
        client_secret=client_secret,
    )


class RuntimeConfigTests(unittest.TestCase):
    def test_cli_values_win_over_environment(self):
        config = resolve_bot_config(
            args('z1r', 'cli-id', 'cli-secret'),
            {
                'TTPBOT_CATEGORY_SLUG': 'env-category',
                'TTPBOT_RACETIME_CLIENT_ID': 'env-id',
                'TTPBOT_RACETIME_CLIENT_SECRET': 'env-secret',
            },
        )

        self.assertEqual(config.category_slug, 'z1r')
        self.assertEqual(config.client_id, 'cli-id')
        self.assertEqual(config.client_secret, 'cli-secret')

    def test_environment_supplies_missing_values(self):
        config = resolve_bot_config(
            args(),
            {
                'TTPBOT_CATEGORY_SLUG': 'z1r',
                'TTPBOT_RACETIME_CLIENT_ID': 'env-id',
                'TTPBOT_RACETIME_CLIENT_SECRET': 'env-secret',
            },
        )

        self.assertEqual(config.category_slug, 'z1r')
        self.assertEqual(config.client_id, 'env-id')
        self.assertEqual(config.client_secret, 'env-secret')

    def test_missing_config_names_uses_env_var_names(self):
        config = resolve_bot_config(
            args(category_slug='z1r'),
            {'TTPBOT_RACETIME_CLIENT_ID': 'env-id'},
        )

        self.assertEqual(
            missing_config_names(config),
            ['TTPBOT_RACETIME_CLIENT_SECRET'],
        )

    def test_blank_values_are_missing(self):
        config = resolve_bot_config(
            args(category_slug='  ', client_id='', client_secret=None),
            {
                'TTPBOT_CATEGORY_SLUG': ' ',
                'TTPBOT_RACETIME_CLIENT_ID': '',
                'TTPBOT_RACETIME_CLIENT_SECRET': '',
            },
        )

        self.assertEqual(
            missing_config_names(config),
            [
                'TTPBOT_CATEGORY_SLUG',
                'TTPBOT_RACETIME_CLIENT_ID',
                'TTPBOT_RACETIME_CLIENT_SECRET',
            ],
        )

    def test_provider_and_service_values_resolve_from_environment(self):
        config = resolve_bot_config(args(), {
            'TTPBOT_RACETIME_ORIGIN': 'https://racetime.z1rracing.com/',
            'TTPBOT_CATEGORY_SLUG': 'z1rr',
            'TTPBOT_RACETIME_CLIENT_ID': 'client-id',
            'TTPBOT_RACETIME_CLIENT_SECRET': 'client-secret',
            'TTPBOT_DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/12345/webhook-token',
            'TTPBOT_RACE_SEEKERS_ROLE_ID': '1494076623442542735',
            'TTPBOT_DATA_DIR': '/var/lib/ttpbot',
            'TTPBOT_ENVIRONMENT': 'production',
        })
        self.assertEqual(config.provider.origin, 'https://racetime.z1rracing.com')
        self.assertEqual(config.provider.category, 'z1rr')
        self.assertEqual(config.category_slug, 'z1rr')
        self.assertEqual(config.data_dir, '/var/lib/ttpbot')
        self.assertEqual(config.environment, 'production')
        self.assertTrue(config.announcements_enabled)
        self.assertNotIn('client-secret', repr(config))
        self.assertNotIn('webhook-token', repr(config))

    def test_explicit_cli_provider_values_win(self):
        cli = argparse.Namespace(
            origin='https://racetime.gg', category_slug='z1rr',
            client_id='cli-id', client_secret='cli-secret',
            discord_webhook_url=None, race_seekers_role_id=None,
            data_dir='C:/ttpbot-data', environment='test',
            allow_insecure_loopback=False, host=None, insecure=False,
        )
        config = resolve_bot_config(cli, {
            'TTPBOT_RACETIME_ORIGIN': 'https://racetime.z1rracing.com',
            'TTPBOT_CATEGORY_SLUG': 'wrong',
            'TTPBOT_RACETIME_CLIENT_ID': 'env-id',
            'TTPBOT_RACETIME_CLIENT_SECRET': 'env-secret',
            'TTPBOT_DATA_DIR': 'C:/wrong',
            'TTPBOT_ENVIRONMENT': 'production',
        })
        self.assertEqual(config.provider.origin, 'https://racetime.gg')
        self.assertEqual(config.provider.category, 'z1rr')
        self.assertEqual(config.client_id, 'cli-id')
        self.assertEqual(config.client_secret, 'cli-secret')
        self.assertEqual(config.environment, 'test')

    def test_production_has_no_origin_default_and_rejects_deprecated_flags(self):
        production = {
            'TTPBOT_CATEGORY_SLUG': 'z1rr',
            'TTPBOT_RACETIME_CLIENT_ID': 'id',
            'TTPBOT_RACETIME_CLIENT_SECRET': 'secret-value',
            'TTPBOT_DATA_DIR': '/var/lib/ttpbot',
            'TTPBOT_ENVIRONMENT': 'production',
        }
        config = resolve_bot_config(args(), production)
        self.assertIn('TTPBOT_RACETIME_ORIGIN', missing_config_names(config))
        self.assertNotIn('secret-value', repr(config))
        deprecated = argparse.Namespace(
            category_slug='z1rr', client_id='id', client_secret='secret-value',
            origin=None, host='localhost:8080', insecure=True,
            data_dir='/var/lib/ttpbot', environment='production',
            discord_webhook_url=None, race_seekers_role_id=None,
            allow_insecure_loopback=False,
        )
        with self.assertRaises(RuntimeConfigurationError) as caught:
            resolve_bot_config(deprecated, production)
        self.assertNotIn('secret-value', str(caught.exception))

    def test_insecure_loopback_is_test_only_and_explicit(self):
        local = argparse.Namespace(
            category_slug='z1rr', client_id='id', client_secret='secret',
            origin='http://127.0.0.1:8080', host=None, insecure=False,
            data_dir='C:/ttpbot-data', environment='test',
            discord_webhook_url=None, race_seekers_role_id=None,
            allow_insecure_loopback=True,
        )
        self.assertFalse(resolve_bot_config(local, {}).provider.secure)
        local.environment = 'production'
        with self.assertRaises(RuntimeConfigurationError):
            resolve_bot_config(local, {})

    def test_announcement_configuration_is_all_or_nothing_and_allowlisted(self):
        base = {
            'TTPBOT_RACETIME_ORIGIN': 'https://racetime.gg',
            'TTPBOT_CATEGORY_SLUG': 'z1rr',
            'TTPBOT_RACETIME_CLIENT_ID': 'id',
            'TTPBOT_RACETIME_CLIENT_SECRET': 'secret',
            'TTPBOT_DATA_DIR': '/var/lib/ttpbot',
            'TTPBOT_ENVIRONMENT': 'production',
        }
        invalid = (
            {'TTPBOT_DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/1/token'},
            {'TTPBOT_RACE_SEEKERS_ROLE_ID': '12345'},
            {'TTPBOT_DISCORD_WEBHOOK_URL': 'https://evil.example/api/webhooks/1/token',
             'TTPBOT_RACE_SEEKERS_ROLE_ID': '12345'},
            {'TTPBOT_DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/1/token',
             'TTPBOT_RACE_SEEKERS_ROLE_ID': 'not-numeric'},
        )
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(RuntimeConfigurationError):
                resolve_bot_config(args(), {**base, **extra})

if __name__ == '__main__':
    unittest.main()
