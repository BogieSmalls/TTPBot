"""The bot's racetime.gg name (TTPBOT_RACETIME_BOT_NAME): ZORA's category calls it Z1RRbot.

TTPBot finds its own chat messages by that name, and a zora room introduces it
by that name. Unset, the name is TTPBot and nothing changes.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ttpbot.config import ZORA_PRESETS
from ttpbot.handler import TTPRaceHandler
from ttpbot.zora import welcome_message
from tests.test_zora_room import CLEARED, ROLLED, zora_handler
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
NAME = 'Z1RRbot'
ZTE5 = ZORA_PRESETS['zte5']
INFO = f'Seed: 1 - Flags: {ZTE5[0]} - ZORA: {ZTE5[1]} - https://zora.gg/'


class BotNameSettingTests(unittest.TestCase):
    def name(self, value=None):
        env = {key: val for key, val in os.environ.items() if key != 'TTPBOT_RACETIME_BOT_NAME'}
        if value is not None:
            env['TTPBOT_RACETIME_BOT_NAME'] = value
        result = subprocess.run([sys.executable, '-c', 'from ttpbot.config import BOT_NAME; print(BOT_NAME)'],
                                capture_output=True, text=True, cwd=ROOT, env=env, check=True)
        return result.stdout.strip()

    def test_unset_or_blank_is_ttpbot(self):
        self.assertEqual(self.name(), 'TTPBot')
        self.assertEqual(self.name('   '), 'TTPBot')

    def test_set_is_that_name(self):
        self.assertEqual(self.name(' Z1RRbot '), 'Z1RRbot')


def said(text, at, bot=None):
    """A chat message from a racer, or from a bot when bot is its name."""
    message = {'is_bot': bot is not None, 'is_system': False, 'posted_at': f'2026-11-20T20:00:{at:02d}+00:00',
               'message': text, 'message_plain': text}
    if bot is None:
        message['user'] = {'name': 'Racer'}
    else:
        message['bot'] = bot
    return message


class RenamedZoraRoomTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for item in (patch('ttpbot.handler.BOT_NAME', NAME),
                     patch.object(TTPRaceHandler, '_log_chat'),
                     patch('ttpbot.handler.asyncio.sleep', new=AsyncMock())):
            item.start()
            self.addCleanup(item.stop)

    def joined_new_room(self):
        handler = zora_handler()
        handler.history_command_cutoff_utc = datetime(2026, 11, 20, 20, 0, 0, tzinfo=timezone.utc)
        return handler

    async def test_the_welcome_says_its_name(self):
        handler = zora_handler()
        with patch.object(TTPRaceHandler, '_handle_recent_history_commands', new=AsyncMock()):
            await handler.chat_history({'messages': []})
        self.assertEqual(handler.messages, [
            "Hi, I'm Z1RRbot. I can help with ZORA seed rolling and hash confirmation. "
            "Type !help to see available commands."])

    async def test_help_says_its_name(self):
        handler = zora_handler()
        await handler.chat_message({'message': said('!help', 1)})
        self.assertTrue(handler.messages[0].startswith('Z1RRbot commands:'), handler.messages[0])

    async def test_a_restart_does_not_answer_commands_it_already_answered(self):
        # Rejoining a room under 90 seconds old: every command here was answered
        # by the bot under its own name, so none may run again, or the
        # !cancel and second !flags would replace the seed.
        handler = self.joined_new_room()
        await handler.chat_history({'messages': [
            said(welcome_message(NAME), 1, bot=NAME),
            said(f'!flags {ZTE5[0]} {ZTE5[1]}', 10), said(INFO, 11, bot=NAME), said(ROLLED, 11, bot=NAME),
            said('!cancel', 20), said(CLEARED, 21, bot=NAME),
            said(f'!flags {ZTE5[0]}', 30), said(INFO, 31, bot=NAME), said(ROLLED, 31, bot=NAME),
        ]})
        self.assertEqual(handler.messages, [])
        self.assertEqual(handler.race_info_updates, [])
        self.assertTrue(handler.seed_rolled)

    async def test_a_restart_answers_a_command_said_after_its_last_message(self):
        handler = self.joined_new_room()
        await handler.chat_history({'messages': [
            said(welcome_message(NAME), 1, bot=NAME), said('!help', 5),
        ]})
        self.assertEqual(len(handler.messages), 1)
        self.assertTrue(handler.messages[0].startswith('Z1RRbot commands:'))


if __name__ == '__main__':
    unittest.main()
