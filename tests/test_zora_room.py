"""A room in ZORA's own category: TTPBot's seed commands with a ZORA flag string.

Driven through chat_message as racetime drives it. ZORA presets come from a
stand-in zora.gg feed or the built-ins; z1r's Council registry is never asked.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from ttpbot.config import ZORA_PRESETS
from ttpbot.handler import TTPRaceHandler
from ttpbot.zora import welcome_message
from tests.test_handler_commands import command_handler
from tests.test_zora_presets import ASNB, FEED, ZTE1, zora_reader

ZORA_WELCOME_MESSAGE = welcome_message('TTPBot')

SEED = 8368178622449674251
ZTE5 = ZORA_PRESETS['zte5']
INFO = ('Seed: 8368178622449674251 - Flags: 143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx - ZORA: 13.7Zg4ZxiL'
        ' - https://zora.gg/?flags=143oNtDD4PAvB%21U8nSk4mxFW0br2w5YUspe7sx&zora=13.7Zg4ZxiL'
        '&seed=8368178622449674251')
ROLLED = 'Seed rolling complete.  See race info for details.'
OTHER_INFO = INFO.replace('8368178622449674251', '1554758598395670874')
PUBLISHED = INFO.replace('8368178622449674251', '1234')
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

    async def test_summary_reads_a_preset_or_the_rooms_flag_pair(self):
        handler = zora_handler()
        await self.say(handler, '!summary zte4')
        self.assertIn('L4 Sword: added (extra floor item in Level 9)', ' '.join(handler.messages))
        handler.messages.clear()
        handler.data['info_bot'] = f'Seed: 1234 - Flags: {ASNB[0]} - ZORA: {ASNB[1]} - https://zora.gg/'
        await self.say(handler, '!summary')
        self.assertTrue(handler.messages[0].startswith('Flags (asnb)'))
        reply = ' '.join(handler.messages)
        for line in ('Progressive Items: On', 'L4 Sword: added (extra item staircase in Level 2)',
                     'Level 9 Entrance: Level 4 sword'):
            self.assertLess(reply.index(line), reply.index('Quest:'))

    async def test_secondary_flags_override_the_base_summary_once_and_fit_in_chat(self):
        handler = zora_handler()
        flags, extra = ZORA_PRESETS['consternationplus']
        await self.say(handler, f'!summary {flags} {extra}')
        reply = ' '.join(handler.messages)
        self.assertTrue(reply.startswith('Flags (consternationplus)'))
        self.assertIn('Randomize Magical Sword: On', reply)
        self.assertEqual(reply.count('Item Shuffle:'), 1)
        self.assertIn('Item Shuffle: Random (No Vanilla)', reply)
        self.assertTrue(all(len(message) <= 500 for message in handler.messages))

    async def test_base_print_quest_info_on_does_not_add_a_zora_rule(self):
        handler = zora_handler()
        # ASNB's base flag string, with only B51 (Print Quest Info) turned on.
        await self.say(handler, '!summary 91250lSP91JdxpR4V55maWlQu8UJ3A none')
        reply = ' '.join(handler.messages)
        self.assertIn('Item Shuffle: Full', reply)
        self.assertNotIn('Spoiler Log', reply)
        self.assertNotIn('Print Quest Info', reply)

    async def test_summary_names_the_room_by_pair_with_loaded_feed_and_old_labels(self):
        self.reader.install(FEED)
        handler = zora_handler()
        for key, name in [('zte1', 'Zora The Explorer Week 1'), ('zte2', 'Zora The Explorer Week 2')]:
            flags, extra = ZORA_PRESETS[key]
            handler.data['info_bot'] = f'{name} - Seed: 1234 - Z1R Flags: {flags} - ZORA Flags: {extra}'
            handler.messages.clear()
            await self.say(handler, '!summary')
            self.assertTrue(handler.messages[0].startswith(f'Flags ({name})'))

    async def test_summary_reads_none_as_defaults_and_keeps_original_builtin_key(self):
        handler = zora_handler()
        for key in ('hopla', 'consternation-noenc'):
            flags, _ = ZORA_PRESETS[key]
            handler.data['info_bot'] = f'Seed: 1234 - Flags: {flags} - ZORA: none - https://zora.gg/'
            handler.messages.clear()
            await self.say(handler, '!summary')
            self.assertTrue(handler.messages[0].startswith(f'Flags ({key})'))
            self.assertNotIn('not readable', ' '.join(handler.messages))

    async def test_unread_zora_flags_leave_the_base_settings_available(self):
        handler = zora_handler()
        for extra in ('99.0', 'oops', '1.zzzzzzz'):
            handler.messages.clear()
            await self.say(handler, f'!summary {ASNB[0]} {extra}')
            reply = ' '.join(handler.messages)
            self.assertIn(f'ZORA flags: {extra} (not readable here)', reply)
            self.assertIn('Quest:', reply)
            self.assertNotIn('Progressive Items:', reply)

    async def test_unlisted_control_combinations_show_their_individual_values(self):
        handler = zora_handler()
        await self.say(handler, f'!summary {ASNB[0]} 4.99')
        reply = ' '.join(handler.messages)
        self.assertIn('Add L4 Sword: Random', reply)
        self.assertIn('Add L4 Sword: Level 2: Off', reply)
        handler.messages.clear()
        # B51=Random with detailed_spoiler_log=On has no combined page value.
        await self.say(handler, '!summary eQ1fE2lfz20zC5NQizeS7W0kI0000 22.7KrCi9O0yDms')
        reply = ' '.join(handler.messages)
        self.assertIn('Print Quest Info: Random', reply)
        self.assertIn('Detailed Spoiler Log: On', reply)

    async def test_its_own_late_completion_does_not_undo_a_cancel(self):
        # A !cancel typed while the bot was still sending its roll is read
        # before the bot's own "Seed rolling complete." comes back from racetime.
        handler = zora_handler()
        await self.say(handler, '!zte5')
        await self.say(handler, '!cancel')
        self.assertEqual(handler.messages[-1], CLEARED)
        await handler.chat_message({'message': {'is_bot': True, 'bot': 'TTPBot', 'message_plain': ROLLED}})
        self.assertFalse(handler.seed_rolled)
        await self.say(handler, '!asnb')
        self.assertEqual(len(handler.race_info_updates), 2)

    async def test_another_bots_completion_still_counts(self):
        handler = zora_handler()
        await handler.chat_message({'message': {'is_bot': True, 'bot': 'ZoraBot', 'message_plain': ROLLED}})
        self.assertTrue(handler.seed_rolled)

    async def test_help_lists_the_zora_commands(self):
        handler = zora_handler()
        await self.say(handler, '!help')
        self.assertEqual(len(handler.messages), 1)
        self.assertIn('!flags <flagstring> [zora flags]', handler.messages[0])
        self.assertIn('!summary [preset or <flagstring> [zora flags]]', handler.messages[0])
        for z1r_only in ('!ttp', '!leagueweek', '!schedule', '!grace', '!z1rr', '!ttpbot', '!matchup'):
            self.assertNotIn(z1r_only, handler.messages[0])


class ZoraRoomHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def history(self, *texts, info=''):
        handler = zora_handler()
        handler.data['info_bot'] = info
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


    async def test_a_seed_in_race_info_stands_when_its_completion_was_never_sent(self):
        # A disconnect after race info was written, before the chat lines went out.
        handler = await self.history(ZORA_WELCOME_MESSAGE, info=INFO)
        self.assertTrue(handler.seed_rolled)
        await self.say_to(handler, '!zte5')
        self.assertEqual(handler.messages[-1], 'A seed has already been rolled for this race.')

    async def test_a_cancel_after_the_seed_was_announced_still_frees_the_room(self):
        handler = await self.history(ZORA_WELCOME_MESSAGE, INFO, ROLLED, CLEARED, info=INFO)
        self.assertFalse(handler.seed_rolled)

    async def test_a_seed_published_after_a_cancel_stands(self):
        handler = await self.history(ZORA_WELCOME_MESSAGE, INFO, ROLLED, CLEARED, info=OTHER_INFO)
        self.assertTrue(handler.seed_rolled)

    async def say_to(self, handler, text):
        with patch.object(TTPRaceHandler, '_log_chat'):
            await handler.chat_message({'message': {'message': text, 'message_plain': text,
                                                    'user': {'name': 'Racer'}}})


class ZoraRoomReconnectTests(unittest.IsolatedAsyncioTestCase):
    """A new handler after a dropped connection: commands wait for the room's history.

    Seed 1234 is in race info, published just before the connection dropped, so
    neither its announcement nor "Seed rolling complete." reached chat.
    """

    def setUp(self):
        for item in (patch('ttpbot.handler.zora_registry', zora_reader()),
                     patch('ttpbot.handler.random.randint', return_value=SEED),
                     patch.object(TTPRaceHandler, '_log_chat'),
                     patch('ttpbot.handler.asyncio.sleep', new=AsyncMock())):
            item.start()
            self.addCleanup(item.stop)

    async def joined(self, info=PUBLISHED):
        handler = zora_handler()
        handler.data = {'name': 'zora/test-room', 'category': {'slug': 'zora'}, 'status': {'value': 'open'},
                        'goal': {'name': 'Beat the game'}, 'info_bot': info}
        handler.ws = AsyncMock()
        await handler.begin()
        return handler

    async def live(self, handler, text, at=40, key=None):
        await handler.chat_message({'message': self.racer(text, at, key)})

    def racer(self, text, at, key=None):
        return {'id': key, 'is_bot': False, 'is_system': False, 'message': text, 'message_plain': text,
                'user': {'name': 'Racer'}, 'posted_at': f'2026-11-20T20:00:{at:02d}+00:00'}

    async def history(self, handler, *extra):
        welcome = {'is_bot': True, 'bot': 'TTPBot', 'message_plain': ZORA_WELCOME_MESSAGE,
                   'posted_at': '2026-11-20T20:00:01+00:00'}
        await handler.chat_history({'messages': [welcome, *extra]})

    async def test_a_preset_after_history_is_refused(self):
        handler = await self.joined()
        await self.history(handler)
        await self.live(handler, '!asnb')
        self.assertEqual(handler.messages, ['A seed has already been rolled for this race.'])
        self.assertEqual(handler.race_info_updates, [])

    async def test_a_preset_before_history_is_refused_once_history_is_in(self):
        handler = await self.joined()
        await self.live(handler, '!asnb')
        self.assertEqual((handler.messages, handler.race_info_updates), ([], []))
        await self.history(handler)
        self.assertEqual(handler.messages, ['A seed has already been rolled for this race.'])
        self.assertEqual(handler.race_info_updates, [])

    async def test_a_command_held_and_replayed_from_history_runs_once(self):
        # In a room under 90 seconds old, history replays the same !help.
        handler = await self.joined()
        handler.history_command_cutoff_utc = datetime(2026, 11, 20, 20, 0, 0, tzinfo=timezone.utc)
        await self.live(handler, '!help', key='m1')
        await self.history(handler, self.racer('!help', 40, 'm1'))
        self.assertEqual(len(handler.messages), 1)

    async def test_a_held_preset_rolls_when_no_seed_stands(self):
        handler = await self.joined(info='')
        await self.live(handler, '!asnb')
        await self.history(handler)
        self.assertEqual(len(handler.race_info_updates), 1)
        self.assertTrue(handler.race_info_updates[0].startswith(f'Seed: {SEED} - Flags: {ASNB[0]} - '))

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
