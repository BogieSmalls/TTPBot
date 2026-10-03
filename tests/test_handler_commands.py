import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from ttpbot.config import TIMEZONE
from ttpbot.handler import TTPRaceHandler


def command_handler():
    handler = object.__new__(TTPRaceHandler)
    handler.sahasrahbot_present = False
    handler.sahasrahbot_overridden = False
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

    async def test_summary_describes_a_flag_string_even_with_sahasrahbot_present(self):
        handler = command_handler()
        handler.sahasrahbot_present = True

        await handler.ex_summary(['CKnGaCG0jI3PvaGohjRZIOxiM8Y9W8GjoIpZfdC'], {})

        self.assertEqual(len(handler.messages), 1)
        self.assertIn('A/C/WS: Bow / Recorder / Ladder', handler.messages[0])
        self.assertEqual(handler.race_info_updates, [])

    async def test_summary_defaults_to_the_rooms_rolled_flags(self):
        handler = command_handler()
        handler.data['info_bot'] = (
            'Test room | Seed: 123 - Flags: 24hJoDaoq92qaumIfio4Qq8LtfU0Xt8tpG3Iafo'
        )

        await handler.ex_summary([], {})

        self.assertTrue(handler.messages[0].startswith('Flags (ttp4rp) 1/2: '))

    async def test_summary_explains_itself_without_flags_to_read(self):
        handler = command_handler()

        await handler.ex_summary([], {})
        await handler.ex_summary(['not-a-flag-string'], {})

        self.assertIn('Usage: !summary', handler.messages[0])
        self.assertEqual(handler.messages[1], "Couldn't read that flag string.")

    async def test_leagueweek_rolls_the_weeks_flagset(self):
        handler = command_handler()

        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.ex_leagueweek6([], {})

        self.assertTrue(handler.seed_rolled)
        # Same shape as !flags, which is what racers type for League weeks.
        self.assertRegex(
            handler.messages[0],
            r'^Seed: \d+ - Flags: CKnGaCG0jI3PvaGohjRZIOxiM8Y9W8GjoIpZfdC$',
        )

    async def test_leagueweek_hands_sahasrahbot_the_flags(self):
        # SahasrahBot has no League presets, so silence would leave no roll.
        handler = command_handler()
        handler.sahasrahbot_present = True

        await handler.ex_leagueweek4([], {})

        self.assertFalse(handler.seed_rolled)
        self.assertEqual(handler.messages, [
            'League Week 4 (TC #29) flags: oJ5LOsot2OL6WwQr15hZEaydnt0!RcZLT7Z7q'
            ' -- roll with !flags oJ5LOsot2OL6WwQr15hZEaydnt0!RcZLT7Z7q'
        ])

    def test_every_league_week_has_a_command_and_a_decodable_flagset(self):
        from ttpbot.config import LEAGUE_WEEKS
        from ttpbot.flag_summary import format_summary

        self.assertEqual(sorted(LEAGUE_WEEKS), list(range(1, 8)))
        for week, (_name, flags) in LEAGUE_WEEKS.items():
            self.assertTrue(hasattr(TTPRaceHandler, f'ex_leagueweek{week}'))
            format_summary(flags)

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
        # SahasrahBot's roll locks rolling too, so an !override cannot add a
        # second seed. TTPBot defers to SahasrahBot regardless, so nothing else changes.
        self.assertTrue(handler.seed_rolled)

    async def test_sahasrahbot_detected_from_live_message(self):
        handler = command_handler()
        handler.sahasrahbot_present = False

        await handler.chat_message({'message': {
            'is_bot': True, 'bot': 'SahasrahBot', 'message_plain': 'Rolling seed...',
        }})

        self.assertTrue(handler.sahasrahbot_present)

    async def test_override_takes_over_from_a_silent_sahasrahbot(self):
        # 2026-10-02: SahasrahBot greeted the Autumn W1-2 room, then ignored
        # three !flags, and TTPBot deferred to it the whole time.
        handler = command_handler()
        handler.sahasrahbot_present = True

        await handler.ex_override([], {'user': {'name': 'Bogie'}})
        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.ex_flags(['oIbnPfPb01Hll3D29Bc2!etrojQOSjJQZUJ3A'], {})

        self.assertIn('Override on', handler.messages[0])
        self.assertRegex(handler.messages[1],
                         r'^Seed: \d+ - Flags: oIbnPfPb01Hll3D29Bc2!etrojQOSjJQZUJ3A$')
        self.assertTrue(handler.seed_rolled)

    async def test_override_survives_sahasrahbot_speaking_again(self):
        handler = command_handler()
        handler.sahasrahbot_present = True
        await handler.ex_override([], {})

        await handler.chat_message({'message': {
            'is_bot': True, 'bot': 'SahasrahBot', 'message_plain': 'Hi!',
        }})

        self.assertFalse(handler.sahasrahbot_present)

    async def test_a_late_sahasrahbot_roll_blocks_a_second_seed(self):
        handler = command_handler()
        handler.sahasrahbot_present = True
        await handler.ex_override([], {})

        await handler.chat_message({'message': {
            'is_bot': True, 'bot': 'SahasrahBot',
            'message_plain': 'Seed rolling complete.  See race info for details.',
        }})
        await handler.ex_flags(['abc'], {})

        self.assertEqual(handler.messages[-1], 'A seed has already been rolled for this race.')

    async def test_override_is_found_again_after_a_restart(self):
        handler = command_handler()
        handler.state = {'welcomed': True}
        handler.ttp_scheduled_room = True
        handler.reminders_sent = set()

        with patch.object(TTPRaceHandler, '_handle_recent_history_commands', new=AsyncMock()):
            await handler.chat_history({'messages': [
                {'is_bot': True, 'bot': 'SahasrahBot', 'message_plain': 'Hi!'},
                {'is_bot': False, 'message': '!override', 'message_plain': '!override'},
            ]})

        self.assertFalse(handler.sahasrahbot_present)
        self.assertTrue(handler.sahasrahbot_overridden)

    async def test_override_without_sahasrahbot_says_ttpbot_is_already_rolling(self):
        handler = command_handler()

        await handler.ex_override([], {})

        self.assertIn('already rolling seeds', handler.messages[0])
        self.assertFalse(handler.sahasrahbot_overridden)

    async def test_info_and_help_reference_ttp5_season(self):
        handler = command_handler()

        await handler.ex_info([], {})
        await handler.ex_help([], {})

        self.assertIn('TTP Season 5 regular season runs Aug 31 - Dec 19, 2026', handler.messages[0])
        self.assertIn('Mon-Sat at 8 PM, 10 PM, 12 AM ET', handler.messages[0])
        self.assertIn('plus 6 PM on Saturday', handler.messages[0])
        self.assertNotIn('12 PM, 3 PM, 6 PM', handler.messages[0])
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

    async def test_torneo_corto_rolls_the_editions_flagset(self):
        handler = command_handler()

        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.ex_tc33([], {})

        self.assertTrue(handler.seed_rolled)
        self.assertRegex(
            handler.messages[0],
            r'^tc33 - Flags: oIbnPfPb0mR7ggXWkGxc3qVN!8mpdjauom05j Seed: \d+$',
        )

    async def test_torneo_corto_hands_sahasrahbot_the_flags(self):
        # SahasrahBot has no Torneo Corto presets, so silence would leave no roll.
        handler = command_handler()
        handler.sahasrahbot_present = True

        await handler.ex_tc33([], {})

        self.assertFalse(handler.seed_rolled)
        self.assertEqual(handler.messages, [
            'Torneo Corto #33 flags: oIbnPfPb0mR7ggXWkGxc3qVN!8mpdjauom05j'
            ' -- roll with !flags oIbnPfPb0mR7ggXWkGxc3qVN!8mpdjauom05j'
        ])

    async def test_torneo_corto_spelled_out_is_the_same_command(self):
        handler = command_handler()
        handler.sahasrahbot_present = True

        await handler.ex_torneocorto31([], {})

        self.assertIn('Torneo Corto #31 flags:', handler.messages[0])

    def test_every_torneo_corto_preset_has_a_command_and_decodes(self):
        from ttpbot.config import PRESET_ALIASES, PRESET_NAMES, SEED_PRESETS
        from ttpbot.flag_summary import format_summary

        editions = [31, 32, 33]
        for edition in editions:
            preset = 'tc%d' % edition
            self.assertIn(preset, SEED_PRESETS)
            self.assertEqual(PRESET_NAMES[preset], 'Torneo Corto #%d' % edition)
            self.assertTrue(hasattr(TTPRaceHandler, 'ex_' + preset))
            # Spelled out resolves both as a command and as a !race preset.
            self.assertTrue(hasattr(TTPRaceHandler, 'ex_torneocorto%d' % edition))
            self.assertEqual(PRESET_ALIASES['torneocorto%d' % edition], preset)
            format_summary(SEED_PRESETS[preset])

        # Each edition is its own flagset; a copy-paste slip would make two the same.
        flags = [SEED_PRESETS['tc%d' % edition] for edition in editions]
        self.assertEqual(len(set(flags)), len(editions))
