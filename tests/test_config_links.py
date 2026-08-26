import importlib
import os
import unittest
from unittest.mock import patch

import ttpbot.config as config_module


class ConfigLinksTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(config_module)

    def test_blank_z1rr_link_environment_uses_defaults(self):
        with patch.dict(os.environ, {
            'TTPBOT_Z1RR_DISCORD_URL': '',
            'TTPBOT_Z1RR_RACEROOM_URL': '   ',
        }):
            config = importlib.reload(config_module)

        self.assertEqual(config.Z1RR_DISCORD_URL, 'https://discord.gg/z1rr')
        self.assertEqual(
            config.Z1RR_RACEROOM_URL,
            'https://raceroom.z1rracing.com/z1rr',
        )


if __name__ == '__main__':
    unittest.main()
