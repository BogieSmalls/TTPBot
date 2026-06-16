import argparse
import unittest

from ttpbot.runtime_config import missing_config_names, resolve_bot_config


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


if __name__ == '__main__':
    unittest.main()
