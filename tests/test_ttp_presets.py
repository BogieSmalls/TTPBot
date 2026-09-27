import unittest
from unittest.mock import AsyncMock, patch

from ttpbot.config import PRESET_ALIASES, SEED_PRESETS, TTP5_PRESETS
from ttpbot.flag_summary import format_summary
from ttpbot.handler import TTPRaceHandler

from tests.test_handler_commands import command_handler

UPHILL = '143oNtDD4Pw3Yw6SRryTCXlqTsRz2fxg4s4pJW'


async def run(handler, command, args=()):
    with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
        await getattr(handler, f'ex_{command}')(list(args), {})


class TTP5PresetTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_ttp5_command_rolls_its_preset(self):
        for command in ('ttp5uphill', 'ttp5muffle', 'ttp5pick5'):
            handler = command_handler()

            await run(handler, command)

            self.assertTrue(handler.seed_rolled)
            self.assertTrue(handler.messages[0].startswith(
                f'{command} - Flags: {SEED_PRESETS[command]} Seed: '
            ))

    async def test_ttp5_picks_one_of_the_three(self):
        handler = command_handler()

        with patch('ttpbot.handler.random.choice', return_value='ttp5pick5') as choice:
            await run(handler, 'ttp5')

        choice.assert_called_once_with(TTP5_PRESETS)
        self.assertTrue(handler.messages[0].startswith('ttp5pick5 - Flags: '))

    async def test_ttp5_hands_sahasrahbot_the_flags_rather_than_rolling(self):
        # SahasrahBot has no TTP5 presets. Rolling would risk two seeds;
        # silence would leave the command unanswered.
        handler = command_handler()
        handler.sahasrahbot_present = True

        await run(handler, 'ttp5uphill')

        self.assertFalse(handler.seed_rolled)
        self.assertEqual(handler.messages, [
            f'TTP5 Uphill Battle flags: {UPHILL} -- roll with !flags {UPHILL}',
        ])


class AliasTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_alias_is_a_command_for_its_preset(self):
        for alias, preset in PRESET_ALIASES.items():
            handler = command_handler()

            await run(handler, alias)

            self.assertTrue(
                handler.messages[0].startswith(f'{preset} - Flags: {SEED_PRESETS[preset]} Seed: '),
                alias,
            )

    async def test_alias_hands_off_with_sahasrahbot_present(self):
        handler = command_handler()
        handler.sahasrahbot_present = True

        await run(handler, 'ttp4rr')

        self.assertTrue(handler.messages[0].startswith('TTP4 Random% Remastered flags: '))

    async def test_canonical_ttp4_commands_still_defer_silently(self):
        # SahasrahBot answers !ttp4rp itself.
        handler = command_handler()
        handler.sahasrahbot_present = True

        await run(handler, 'ttp4rp')

        self.assertEqual(handler.messages, [])

    async def test_race_accepts_an_alias(self):
        handler = command_handler()

        await run(handler, 'race', ['TTP5UphillBattle'])

        self.assertTrue(handler.messages[0].startswith(f'ttp5uphill - Flags: {UPHILL} Seed: '))

    def test_aliases_point_at_real_presets_and_do_not_shadow_commands(self):
        for alias, preset in PRESET_ALIASES.items():
            self.assertIn(preset, SEED_PRESETS)
            self.assertNotIn(alias, SEED_PRESETS)


class SummaryByPresetTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_accepts_a_preset_or_alias(self):
        for name in ('ttp5uphill', 'TTP5UphillBattle', 'leagueweek4'):
            handler = command_handler()

            await run(handler, 'summary', [name])

            preset = PRESET_ALIASES.get(name.lower(), name.lower())
            self.assertEqual(handler.messages, format_summary(SEED_PRESETS[preset]), name)


class TTPFlagsTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_ttp5_presets_and_the_season_rules(self):
        handler = command_handler()

        await run(handler, 'ttpflags')

        text = handler.messages[0]
        for expected in ('!ttp5 ', '!ttp5uphill -- Uphill Battle', '!ttp5muffle -- Muffle Rug',
                         '!ttp5pick5 -- Pick 5', 'mutual agreement'):
            self.assertIn(expected, text)
        self.assertNotIn('ttp4', text)

    def test_ttp5_presets_decode(self):
        for preset in TTP5_PRESETS:
            format_summary(SEED_PRESETS[preset])
        self.assertTrue(hasattr(TTPRaceHandler, 'ex_ttp5mufflerug'))


if __name__ == '__main__':
    unittest.main()
