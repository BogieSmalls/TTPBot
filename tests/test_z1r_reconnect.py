"""Reconnect ordering through the same begin/history/chat entry points as racetime."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from tests.test_handler_commands import FakeWebSocket, command_handler
from ttpbot.handler import GENERIC_WELCOME_MESSAGE, TTPRaceHandler


FLAGS = '143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx'
ROLLED = 'Seed rolling complete.  See race info for details.'


def say(text, key, second, bot=None):
    return {'message': text, 'message_plain': text, 'id': key, 'is_bot': bool(bot), 'is_system': False,
            'bot': bot, 'user': None if bot else {'name': 'Racer'},
            'posted_at': f'2026-11-20T20:00:{second:02d}+00:00'}


WELCOME = say(GENERIC_WELCOME_MESSAGE, 'welcome', 1, 'TTPBot')
COMPLETION = say(ROLLED, 'completion', 2, 'TTPBot')


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for item in (patch.object(TTPRaceHandler, '_log_chat'),
                     patch('ttpbot.handler.asyncio.sleep', new=AsyncMock())):
            item.start()
            self.addCleanup(item.stop)

    async def joined(self, published=''):
        handler = command_handler()
        handler.data = {'name': 'z1r/test-room', 'category': {'slug': 'z1r'}, 'status': {'value': 'open'},
                        'goal': {'name': 'Beat the Game (Casual)'}, 'info_bot': published}
        handler.reminders_sent = set()
        handler.ws = FakeWebSocket()
        handler.state = {}
        await handler.begin()
        return handler

    async def test_standing_seed_survives_both_orders_of_command_and_history(self):
        info = f'Seed: 1234 - Flags: {FLAGS}'
        for live_first in (True, False):
            with self.subTest(live_first=live_first):
                handler = await self.joined(info)
                incoming = {'message': say(f'!flags {FLAGS}', 'flags', 10)}
                if live_first:
                    await handler.chat_message(incoming)
                    self.assertEqual(handler.race_info_updates, [])
                await handler.chat_history({'messages': [WELCOME, COMPLETION]})
                if not live_first:
                    await handler.chat_message(incoming)
                self.assertEqual(handler.messages, ['A seed has already been rolled for this race.'])
                self.assertEqual(handler.race_info_updates, [])

    async def test_replayed_cancel_then_live_flags_runs_once_for_each_id(self):
        handler = await self.joined(f'Seed: 1234 - Flags: {FLAGS}')
        handler.history_command_cutoff_utc = datetime(2026, 11, 20, 20, tzinfo=timezone.utc)
        cancel = say('!cancel', 'cancel', 8)
        roll = say(f'!flags {FLAGS}', 'flags', 10)
        await handler.chat_message({'message': roll})
        await handler.chat_history({'messages': [roll, WELCOME, cancel, COMPLETION]})
        await handler.chat_message({'message': cancel})
        self.assertEqual(len(handler.race_info_updates), 1)
        self.assertEqual(handler.messages[0], 'Seed cleared. You may now roll a new one.')
        self.assertEqual(handler.messages[-1], ROLLED)
        self.assertTrue(handler.seed_rolled)

    async def test_late_own_completion_keeps_a_cancel_open_and_foreign_completion_still_counts(self):
        handler = await self.joined()
        await handler.chat_history({'messages': [WELCOME]})
        await handler.chat_message({'message': say(f'!flags {FLAGS}', 'first', 4)})
        await handler.chat_message({'message': say('!cancel', 'cancel', 8)})
        await handler.chat_message({'message': COMPLETION})
        await handler.chat_message({'message': say(f'!flags {FLAGS}', 'second', 10)})
        self.assertEqual(len(handler.race_info_updates), 2)
        await handler.chat_message({'message': say('!cancel', 'cancel-again', 11)})
        await handler.chat_message({'message': say(ROLLED, 'another-bot', 12, 'SahasrahBot')})
        self.assertTrue(handler.seed_rolled)
        self.assertTrue(handler.sahasrahbot_present)
