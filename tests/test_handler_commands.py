import unittest
from unittest.mock import AsyncMock, Mock, patch

from ttpbot.handler import TTPRaceHandler


def command_handler():
    handler = object.__new__(TTPRaceHandler)
    handler.seed_rolled = False
    handler.data = {'name': 'z1rr/test-room', 'info_bot': 'Test room'}
    handler.logger = Mock()
    handler.messages = []
    handler.race_info_updates = []

    async def send_message(message):
        handler.messages.append(message)

    async def set_bot_raceinfo(value):
        handler.race_info_updates.append(value)

    handler.send_message = send_message
    handler.set_bot_raceinfo = set_bot_raceinfo
    return handler


class HandlerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_z1rr_command_posts_discord_and_raceroom_links(self):
        handler = command_handler()

        await handler.ex_z1rr([], {})

        self.assertEqual(len(handler.messages), 1)
        self.assertIn('Z1RR Discord', handler.messages[0])
        self.assertIn('discord.gg', handler.messages[0])
        self.assertIn('raceroom.z1rracing.com', handler.messages[0])

    async def test_info_and_help_reference_ttp5_season(self):
        handler = command_handler()

        await handler.ex_info([], {})
        await handler.ex_help([], {})

        self.assertIn('TTP Season 5 regular season runs Aug 31 - Dec 19, 2026', handler.messages[0])
        self.assertIn('Mon-Fri at 8 PM, 10 PM, 12 AM ET', handler.messages[0])
        self.assertIn('Sat at 12 PM, 3 PM, 6 PM ET', handler.messages[0])
        self.assertIn('TTP Season 5 details', handler.messages[1])

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
