import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from ttpbot.config import TIMEZONE
from ttpbot.handler import TTPRaceHandler


def command_handler():
    handler = object.__new__(TTPRaceHandler)
    handler.sahasrahbot_present = False
    handler.seed_rolled = False
    handler.data = {'name': 'z1rr/test-room', 'info_bot': 'Test room'}
    handler.logger = Mock()
    handler.command_prefix = '!'
    handler.messages = []
    handler.race_info_updates = []

    async def send_message(message):
        handler.messages.append(message)

    async def set_bot_raceinfo(value):
        handler.race_info_updates.append(value)

    handler.send_message = send_message
    handler.set_bot_raceinfo = set_bot_raceinfo
    return handler


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class HandlerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_z1rr_command_posts_discord_invite(self):
        handler = command_handler()

        await handler.ex_z1rr([], {})

        self.assertEqual(
            handler.messages,
            ['Join the Z1RR Discord! https://discord.gg/MX6EB26HYB'],
        )

    async def test_welcome_message_avoids_triforce_emote_token(self):
        handler = command_handler()
        handler.state = {}
        handler.reminders_sent = set()
        handler.ttp_scheduled_room = True

        await handler.chat_history({'messages': []})

        self.assertEqual(len(handler.messages), 1)
        self.assertIn('Welcome to TTP Season 5!', handler.messages[0])
        self.assertNotIn('Triforce', handler.messages[0])

    async def test_chat_history_recognizes_existing_ttp5_welcome(self):
        handler = command_handler()
        handler.state = {}
        handler.reminders_sent = set()
        handler.ttp_scheduled_room = True

        await handler.chat_history({
            'messages': [{
                'is_bot': True,
                'bot': 'TTPBot',
                'message_plain': 'Welcome to TTP Season 5! Already here.',
            }],
        })

        self.assertEqual(handler.messages, [])
        self.assertTrue(handler.state['welcomed'])

    async def test_casual_room_begin_skips_ttp_schedule_state(self):
        handler = command_handler()
        handler.data = {
            'name': 'z1rr/casual-room',
            'goal': {'name': 'Beat The Game (Casual)'},
            'info_bot': 'Casual open room',
        }
        handler.state = {}
        handler.ws = FakeWebSocket()
        handler.reminders_sent = set()
        handler.scheduled_time = None
        handler.bot_created = False
        handler.reminder_task = None

        nearest_ttp_race = datetime(2026, 8, 31, 20, 0, tzinfo=TIMEZONE)
        with patch('ttpbot.handler.find_nearest_scheduled_race', return_value=nearest_ttp_race):
            await handler.begin()

        self.assertFalse(handler.ttp_scheduled_room)
        self.assertIsNone(handler.scheduled_time)
        self.assertFalse(handler.bot_created)
        self.assertNotIn('scheduled_time', handler.state)
        self.assertEqual(handler.ws.sent, ['{"action": "gethistory"}'])

    async def test_casual_room_history_sends_generic_welcome(self):
        handler = command_handler()
        handler.state = {}
        handler.reminders_sent = set()
        handler.ttp_scheduled_room = False

        await handler.chat_history({'messages': []})

        self.assertEqual(
            handler.messages,
            [
                "Hi, I'm TTPBot. I can help with seed rolling, hash confirmation, "
                "and Z1RR links. Type !help to see available commands."
            ],
        )
        self.assertTrue(handler.state['welcomed'])

    async def test_chat_history_handles_recent_command_before_room_attach(self):
        handler = command_handler()
        handler.state = {}
        handler.reminders_sent = set()
        handler.ttp_scheduled_room = False
        handler.history_command_cutoff_utc = datetime(
            2026, 8, 27, 4, 28, 30, tzinfo=timezone.utc,
        )

        await handler.chat_history({
            'messages': [{
                'is_bot': False,
                'is_system': False,
                'posted_at': '2026-08-27T04:29:02.469241+00:00',
                'message': '!z1rr',
                'message_plain': '!z1rr',
                'user': {'name': 'Bogie'},
            }],
        })

        self.assertEqual(
            handler.messages,
            [
                "Hi, I'm TTPBot. I can help with seed rolling, hash confirmation, "
                "and Z1RR links. Type !help to see available commands.",
                'Join the Z1RR Discord! https://discord.gg/MX6EB26HYB',
            ],
        )

    async def test_casual_room_history_recognizes_existing_generic_welcome(self):
        handler = command_handler()
        handler.state = {}
        handler.reminders_sent = set()
        handler.ttp_scheduled_room = False

        await handler.chat_history({
            'messages': [{
                'is_bot': True,
                'bot': 'TTPBot',
                'message_plain': (
                    "Hi, I'm TTPBot. I can help with seed rolling, hash "
                    "confirmation, and Z1RR links. Type !help to see available "
                    "commands."
                ),
            }],
        })

        self.assertEqual(handler.messages, [])
        self.assertTrue(handler.state['welcomed'])

    async def test_recent_history_does_not_replay_commands_before_bot_welcome(self):
        handler = command_handler()
        handler.state = {}
        handler.reminders_sent = set()
        handler.ttp_scheduled_room = False
        handler.history_command_cutoff_utc = datetime(
            2026, 8, 27, 4, 28, 55, tzinfo=timezone.utc,
        )

        await handler.chat_history({
            'messages': [
                {
                    'is_bot': False,
                    'is_system': False,
                    'posted_at': '2026-08-27T04:29:02.469241+00:00',
                    'message': '!z1rr',
                    'message_plain': '!z1rr',
                    'user': {'name': 'Bogie'},
                },
                {
                    'is_bot': True,
                    'bot': 'TTPBot',
                    'posted_at': '2026-08-27T04:29:24.950000+00:00',
                    'message_plain': (
                        "Hi, I'm TTPBot. I can help with seed rolling, hash "
                        "confirmation, and Z1RR links. Type !help to see "
                        "available commands."
                    ),
                },
            ],
        })

        self.assertEqual(handler.messages, [])
        self.assertTrue(handler.state['welcomed'])

    async def test_seed_commands_defer_to_sahasrahbot_when_present(self):
        handler = command_handler()
        handler.sahasrahbot_present = True

        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.ex_race(['ttp4rp'], {})
            await handler.ex_flags(['abc'], {})
            await handler.ex_ttp4([], {})
            await handler.ex_ttp4rp([], {})

        self.assertEqual(handler.messages, [])
        self.assertEqual(handler.race_info_updates, [])
        self.assertFalse(handler.seed_rolled)

    async def test_informational_commands_answer_even_with_sahasrahbot_present(self):
        handler = command_handler()
        handler.sahasrahbot_present = True

        await handler.ex_help([], {})
        await handler.ex_info([], {})

        self.assertEqual(len(handler.messages), 2)
        self.assertIn('TTPBot commands:', handler.messages[0])

    async def test_sahasrahbot_detected_from_chat_history(self):
        handler = command_handler()
        handler.sahasrahbot_present = False
        handler.state = {'welcomed': True}
        handler.ttp_scheduled_room = True
        handler.reminders_sent = set()

        with patch.object(TTPRaceHandler, '_handle_recent_history_commands', new=AsyncMock()):
            await handler.chat_history({'messages': [
                {'is_bot': True, 'bot': 'SahasrahBot', 'message_plain': 'Seed rolling complete.'},
            ]})

        self.assertTrue(handler.sahasrahbot_present)
        # SahasrahBot's own roll must not be mistaken for one of TTPBot's.
        self.assertFalse(handler.seed_rolled)

    async def test_sahasrahbot_detected_from_live_message(self):
        handler = command_handler()
        handler.sahasrahbot_present = False

        await handler.chat_message({'message': {
            'is_bot': True, 'bot': 'SahasrahBot', 'message_plain': 'Rolling seed...',
        }})

        self.assertTrue(handler.sahasrahbot_present)

    async def test_info_and_help_reference_ttp5_season(self):
        handler = command_handler()

        await handler.ex_info([], {})
        await handler.ex_help([], {})

        self.assertIn('TTP Season 5 regular season runs Aug 31 - Dec 19, 2026', handler.messages[0])
        self.assertIn('Mon-Fri at 8 PM, 10 PM, 12 AM ET', handler.messages[0])
        self.assertIn('Sat at 12 PM, 3 PM, 6 PM ET', handler.messages[0])
        self.assertIn('TTP Season 5 goal', handler.messages[0])
        self.assertIn('TTP Season 5 details', handler.messages[1])
        self.assertIn('!z1rr                       Z1RR Discord invite', handler.messages[1])

    async def test_race_command_rolls_from_named_preset(self):
        handler = command_handler()

        with (
            patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()),
            patch('ttpbot.handler.random.randint', return_value=123456789),
        ):
            await handler.ex_race(['rr2025'], {})

        self.assertTrue(handler.seed_rolled)
        self.assertEqual(
            handler.race_info_updates,
            ['Test room | Flags: CKnGZ6u7XaVW!hJ!sGTvkRim82t8PvIW1BEycZo Seed: 123456789'],
        )
        self.assertIn(
            'rr2025 - Flags: CKnGZ6u7XaVW!hJ!sGTvkRim82t8PvIW1BEycZo Seed: 123456789',
            handler.messages,
        )

    async def test_ttp_shortcut_rolls_from_curated_pool(self):
        handler = command_handler()

        with (
            patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()),
            patch('ttpbot.handler.random.choice', return_value='ttp4rp'),
            patch('ttpbot.handler.random.randint', return_value=987654321),
        ):
            await handler.ex_ttp4([], {})

        self.assertTrue(handler.seed_rolled)
        self.assertIn(
            'ttp4rp - Flags: 24hJoDaoq92qaumIfio4Qq8LtfU0Xt8tpG3Iafo Seed: 987654321',
            handler.messages,
        )

    async def test_chat_history_seed_lock_uses_prior_bot_seed_roll(self):
        handler = command_handler()
        handler.state = {'welcomed': True}
        handler.reminders_sent = set()
        handler.ttp_scheduled_room = True

        await handler.chat_history({
            'messages': [{
                'is_bot': True,
                'bot': 'TTPBot',
                'message_plain': 'Seed rolling complete.  See race info for details.',
            }],
        })

        self.assertTrue(handler.seed_rolled)


if __name__ == '__main__':
    unittest.main()
