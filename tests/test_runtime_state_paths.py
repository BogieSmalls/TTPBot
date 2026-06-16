import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class RuntimeStatePathTests(unittest.TestCase):
    def test_bot_state_files_use_ttpbot_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': tmp}):
                import ttpbot.bot as bot_module
                importlib.reload(bot_module)

                self.assertEqual(Path(bot_module.CREATED_RACES_FILE), Path(tmp) / 'created_races.json')
                self.assertEqual(Path(bot_module.SENT_WEBHOOKS_FILE), Path(tmp) / 'sent_webhooks.json')

    def test_handler_state_files_use_ttpbot_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': tmp}):
                import ttpbot.handler as handler_module
                importlib.reload(handler_module)

                self.assertEqual(Path(handler_module.CHAT_LOG_DIR), Path(tmp) / 'chat_logs')
                self.assertEqual(Path(handler_module.LEARNED_ALIASES_FILE), Path(tmp) / 'learned_aliases.json')

    def test_save_learned_alias_creates_data_dir_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / 'state'
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': str(nested)}):
                import ttpbot.handler as handler_module
                importlib.reload(handler_module)

                handler_module._save_learned_alias('trianlge', 'Triforce')

                self.assertTrue((nested / 'learned_aliases.json').is_file())

    def test_save_created_races_creates_data_dir_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / 'state'
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': str(nested)}):
                import ttpbot.bot as bot_module
                importlib.reload(bot_module)

                bot = object.__new__(bot_module.TTPBot)
                bot.created_races = {'2026-06-16T20:00:00-04:00': 'https://racetime.gg/z1r/test'}
                bot.logger = Mock()

                bot._save_created_races()

                self.assertTrue((nested / 'created_races.json').is_file())

    def test_save_sent_webhooks_creates_data_dir_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / 'state'
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': str(nested)}):
                import ttpbot.bot as bot_module
                importlib.reload(bot_module)

                bot = object.__new__(bot_module.TTPBot)
                bot.sent_webhooks = {'2026-06-16T20:00:00-04:00'}
                bot.logger = Mock()

                bot._save_sent_webhooks()

                self.assertTrue((nested / 'sent_webhooks.json').is_file())


if __name__ == '__main__':
    unittest.main()
