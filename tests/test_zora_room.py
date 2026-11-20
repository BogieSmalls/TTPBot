"""A room in ZORA's own category: TTPBot's seed commands with a ZORA flag string.

Driven through chat_message as racetime drives it. ZORA presets come from a
stand-in zora.gg feed or the built-ins; z1r's Council registry is never asked.
"""

import unittest
from unittest.mock import AsyncMock, patch

from ttpbot.config import ZORA_PRESETS
from ttpbot.handler import TTPRaceHandler
from ttpbot.zora import ZORA_WELCOME_MESSAGE
from tests.test_handler_commands import command_handler
from tests.test_zora_presets import ASNB, FEED, ZTE1, zora_reader

SEED = 8368178622449674251
ZTE5 = ZORA_PRESETS['zte5']
INFO = ('Seed: 8368178622449674251 - Flags: 143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx - ZORA: 13.7Zg4ZxiL'
        ' - https://zora.gg/?flags=143oNtDD4PAvB%21U8nSk4mxFW0br2w5YUspe7sx&zora=13.7Zg4ZxiL'
        '&seed=8368178622449674251')
ROLLED = 'Seed rolling complete.  See race info for details.'
CLEARED = 'Seed cleared. You may now roll a new one.'


def zora_handler():
    handler = command_handler()
    handler.zora_room = True
    handler.state = {}
    handler.reminders_sent = set()
    handler.data = {'name': 'zora/test-room', 'info_bot': ''}
    return handler


class ZoraRoomTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reader = zora_reader()
        for item in (patch('ttpbot.handler.zora_registry', self.reader),
                     patch('ttpbot.handler.random.randint', return_value=SEED),
                     patch.object(TTPRaceHandler, '_log_chat'),
                     patch('ttpbot.handler.asyncio.sleep', new=AsyncMock())):
            item.start()
            self.addCleanup(item.stop)
        # z1r's registry must never answer in a zora room.
        council = patch('ttpbot.handler.registry')
        self.council = council.start()
        self.addCleanup(council.stop)

    async def say(self, handler, text):
        await handler.chat_message({'message': {'message': text, 'message_plain': text, 'user': {'name': 'Racer'}}})

    def assert_not_rolled(self, handler):
        self.assertFalse(handler.seed_rolled)
        self.assertEqual(handler.race_info_updates, [])

    async def test_flags_with_both_strings(self):
        handler = zora_handler()
        await self.say(handler, '!FLAGS 143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx 13.7Zg4ZxiL')
        self.assertEqual(handler.race_info_updates, [INFO])
        self.assertEqual(handler.messages, [INFO, ROLLED])
        self.assertTrue(handler.seed_rolled)
        self.council.assert_not_called()
        self.assertEqual(self.council.method_calls, [])

    async def test_flags_with_the_z1r_string_alone(self):
        handler = zora_handler()
        await self.say(handler, '!flags oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW')
        self.assertEqual(handler.race_info_updates, [
            f'Seed: {SEED} - Flags: oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW - ZORA: none'
            f' - https://zora.gg/?flags=oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW&seed={SEED}'])

    async def test_a_zora_string_written_with_underscores_reads_as_bangs(self):
        handler = zora_handler()
        await self.say(handler, '!flags oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW 13.ab_c')
        self.assertIn(' - ZORA: 13.ab!c - ', handler.race_info_updates[0])

    async def test_not_a_zora_string(self):
        handler = zora_handler()
        await self.say(handler, '!flags oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW T9Jvg')
        self.assertEqual(handler.messages,
                         ['"T9Jvg" is not a ZORA flag string. Usage: !flags <flagstring> [zora flags]'])
        self.assert_not_rolled(handler)

    async def test_z1r_string_checks_are_z1rs(self):
        for text, reply in (
            ('!flags', 'You must specify a set of flags!'),
            ("!flags doesn't", '"doesn\'t" is not a flag string. Usage: !flags <flagstring> [zora flags]'),
            ('!flags oIbnPf', '"oIbnPf" is too short to be a flag string. Usage: !flags <flagstring> [zora flags]'),
            ('!flags asnb', '"asnb" is a shortcut, not a flag string. Use !asnb to roll it.'),
        ):
            handler = zora_handler()
            await self.say(handler, text)
            self.assertEqual(handler.messages, [reply], text)
            self.assert_not_rolled(handler)

    async def test_a_registered_short_string_rolls(self):
        self.reader.install(FEED)
        handler = zora_handler()
        await self.say(handler, '!flags CEERPCkLslB6BApr1Him1SiR28Y')
        self.assertTrue(handler.seed_rolled)

    async def test_one_roll_until_cancel_and_the_next_roll_replaces_race_info(self):
        handler = zora_handler()
        await self.say(handler, '!flags 143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx 13.7Zg4ZxiL')
        handler.data['info_bot'] = handler.race_info_updates[-1]
        await self.say(handler, '!race asnb')
        self.assertEqual(handler.messages[-1], 'A seed has already been rolled for this race.')
        await self.say(handler, '!cancel')
        self.assertEqual(handler.messages[-1], CLEARED)
        self.assertFalse(handler.seed_rolled)
        await self.say(handler, '!race asnb')
        self.assertTrue(handler.race_info_updates[-1].startswith(f'Seed: {SEED} - Flags: {ASNB[0]} - ZORA: {ASNB[1]} - '))
        self.assertEqual(handler.race_info_updates[-1].count('Seed:'), 1)

    async def test_race_rolls_a_built_in_preset(self):
        handler = zora_handler()
        await self.say(handler, '!race ZTE5')
        self.assertEqual(handler.race_info_updates, [INFO])
        self.assertEqual(handler.messages, [f'zte5 - {INFO}', ROLLED])

    async def test_race_rolls_from_a_loaded_feed(self):
        self.reader.install(FEED)
        handler = zora_handler()
        # !race reads one word, as in z1r; a name matches without spaces or case.
        await self.say(handler, '!race AllSwordsNoBoards')
        self.assertTrue(handler.race_info_updates[0].startswith(f'Seed: {SEED} - Flags: {ASNB[0]} - ZORA: {ASNB[1]} - '))
        handler = zora_handler()
        await self.say(handler, '!race zte5')
        self.assertEqual(handler.messages, ['Unknown preset "zte5". See https://zora.gg/ for the presets.'])

    async def test_race_without_or_with_an_unknown_preset(self):
        handler = zora_handler()
        await self.say(handler, '!race')
        await self.say(handler, '!race ttp5uphill')
        self.assertEqual(handler.messages, ['No preset specified. See https://zora.gg/ for the presets.',
                                            'Unknown preset "ttp5uphill". See https://zora.gg/ for the presets.'])
        self.assert_not_rolled(handler)

    async def test_a_preset_shortcut_is_a_command(self):
        self.reader.install(FEED)
        handler = zora_handler()
        await self.say(handler, '!zte1')
        self.assertIn(f'Flags: {ZTE1[0]} - ZORA: {ZTE1[1]} - ', handler.race_info_updates[0])
        self.assertEqual(handler.messages[0].split(' - ')[0], 'zte1')

    async def test_a_preset_name_alone_is_not_a_command(self):
        self.reader.install(FEED)
        handler = zora_handler()
        await self.say(handler, '!hoplaaa')
        await self.say(handler, '!allswordsnoboards')
        self.assertEqual(handler.messages, [])

    async def test_z1r_only_commands_are_silent(self):
        for command in ('!ttp5', '!ttp4', '!ttp5uphill', '!leagueweek3', '!tc34', '!torneocorto34', '!ttp4rr',
                        '!consternation-plus-typo', '!schedule', '!info', '!ttpflags', '!matchup a b', '!z1rr',
                        '!ttpbot', '!optout', '!optin', '!grace', '!presets', '!lock', '!unlock'):
            handler = zora_handler()
            await self.say(handler, command)
            self.assertEqual(handler.messages, [], command)
            self.assert_not_rolled(handler)

    async def test_cancel_with_nothing_rolled(self):
        handler = zora_handler()
        await self.say(handler, '!cancel')
        self.assertEqual(handler.messages, ['No seed has been rolled yet.'])

    async def test_summary_reads_zora_presets_and_the_rooms_z1r_string(self):
        handler = zora_handler()
        with patch('ttpbot.handler.format_summary', return_value=['summary']) as summary:
            await self.say(handler, '!summary zte5')
            summary.assert_called_with(ZTE5[0])
            handler.data['info_bot'] = INFO
            await self.say(handler, '!summary')
            summary.assert_called_with(ZTE5[0])

    async def test_help_lists_the_zora_commands(self):
        handler = zora_handler()
        await self.say(handler, '!help')
        self.assertEqual(len(handler.messages), 1)
        self.assertIn('!flags <flagstring> [zora flags]', handler.messages[0])
        for z1r_only in ('!ttp', '!leagueweek', '!schedule', '!grace', '!z1rr', '!ttpbot', '!matchup'):
            self.assertNotIn(z1r_only, handler.messages[0])


class ZoraRoomHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def history(self, *texts):
        handler = zora_handler()
        messages = [{'is_bot': True, 'bot': 'TTPBot', 'message_plain': text} for text in texts]
        with patch.object(TTPRaceHandler, '_handle_recent_history_commands', new=AsyncMock()):
            await handler.chat_history({'messages': messages})
        return handler

    async def test_welcomes_once(self):
        handler = await self.history()
        self.assertEqual(handler.messages, [ZORA_WELCOME_MESSAGE])
        handler = await self.history(ZORA_WELCOME_MESSAGE)
        self.assertEqual(handler.messages, [])

    async def test_a_standing_roll_blocks_rolling_after_a_restart(self):
        handler = await self.history(ZORA_WELCOME_MESSAGE, INFO, ROLLED)
        self.assertTrue(handler.seed_rolled)

    async def test_a_cancelled_roll_does_not(self):
        handler = await self.history(ZORA_WELCOME_MESSAGE, INFO, ROLLED, CLEARED)
        self.assertFalse(handler.seed_rolled)
        handler = await self.history(ZORA_WELCOME_MESSAGE, ROLLED, CLEARED, ROLLED)
        self.assertTrue(handler.seed_rolled)


class ZoraRoomBeginTests(unittest.IsolatedAsyncioTestCase):
    async def begin(self, data):
        handler = TTPRaceHandler.__new__(TTPRaceHandler)
        handler.data = data
        handler.state = {}
        handler.ws = AsyncMock()
        handler.logger = unittest.mock.Mock()
        handler.reminders_sent = set()
        await handler.begin()
        return handler

    async def test_begin_knows_a_zora_room(self):
        handler = await self.begin({'name': 'zora/clever-link-1234', 'category': {'slug': 'zora'},
                                    'status': {'value': 'open'}, 'goal': {'name': 'Beat the game'}})
        self.assertTrue(handler.zora_room)
        self.assertFalse(handler.ttp_scheduled_room or handler.league_room or handler.autumn_room
                         or handler.corto_room or handler.ttp_tournament_room)

    async def test_begin_knows_a_z1r_room_is_not(self):
        handler = await self.begin({'name': 'z1r/clever-link-1234', 'category': {'slug': 'z1r'},
                                    'status': {'value': 'open'}, 'goal': {'name': 'Beat the game'}})
        self.assertFalse(handler.zora_room)


if __name__ == '__main__':
    unittest.main()
