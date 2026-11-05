import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ttpbot.config import LEAGUE_WEEKS, PRESET_ALIASES, SEED_PRESETS
from ttpbot.flag_summary import format_summary
from ttpbot.flagset_registry import FlagsetRegistry, reduce, validate_snapshot
from ttpbot.handler import TTPRaceHandler
from tests.test_handler_commands import command_handler

CONTRACT = json.loads((Path(__file__).parent / 'fixtures' / 'flagsets-contract.json').read_text(encoding='utf-8'))

CONSTERNATION = 'oIbnPfPb01Hll3DXSuaYUK63ewxQii0LiNxS6'
OLD_CONSTERNATION = 'oIbnPfPb01Hll3D29Bc2!etrojQOSjJQZUJ3A'
TC34 = 'oIbnRjuQKp1DXiYCys9LwvSpzqZbXk1pRmZzr'
TC35 = 'oIbnPfPb0mR7ggXWkGxc3qVN!8mpdjauom05k'
MUFFLE = '2487lt0moGMFabcdefghijklmnopqrstuvw12'
UPHILL = '2487lt0moGMFabcdefghijklmnopqrstuvw13'
PICK5 = '2487lt0moGMFabcdefghijklmnopqrstuvw14'
NEW_WISDOM = 'oIbnPfPb0mR7ggY12zwI0QNIY620UnhU8kiC4'
UNREGISTERED = 'oIbnPfPb0mR7ggY12zwI0QNIY620UnhU8kiZZ'
VANILLA = '8m6YW18uL'


def flagset(name, shortcuts, *strings, latest=None):
    latest = latest or strings[0]
    return {'name': name, 'description': None, 'shortcuts': list(shortcuts),
            'strings': [{'flags': flags, 'isLatest': flags == latest} for flags in strings]}


def snapshot(*extra, revision=1):
    return {'schemaVersion': 1, 'revision': revision, 'updatedAt': '2026-11-06T17:00:00.000Z', 'flagsets': [
        flagset('Consternation', ['consternation'], CONSTERNATION, OLD_CONSTERNATION),
        flagset('Torneo Corto #34', ['tc34', 'torneocorto34', 'corto34'], TC34),
        flagset('Torneo Corto #35', ['tc35'], TC35),
        flagset('TTP5 Muffle Rug', ['ttp5muffle', 'mufflerug', '2026 TTP5 Muffle Rug'], MUFFLE),
        flagset('TTP5 Uphill Battle', ['ttp5uphill', 'uphillbattle'], UPHILL),
        flagset('TTP5 Pick 5', ['ttp5pick5', 'pick5'], PICK5),
        flagset('TTP3 Wisdom', ['wisdom', 'leagueweek1'], NEW_WISDOM, LEAGUE_WEEKS[1][1]),
        flagset('Vanilla', [], VANILLA),
        *extra,
    ]}


def fresh(snap=None, cache_path=None):
    registry = FlagsetRegistry('https://z1rracing.test/flagsets.json', cache_path, SEED_PRESETS, PRESET_ALIASES)
    if snap:
        registry.install(snap)
    return registry


class use:
    """Put a registry behind the handler and the summary for one test."""

    def __init__(self, registry):
        self.patches = [patch('ttpbot.handler.registry', registry), patch('ttpbot.flag_summary.registry', registry)]

    def __enter__(self):
        for item in self.patches:
            item.start()

    def __exit__(self, *_):
        for item in self.patches:
            item.stop()


class FakeResponse:
    def __init__(self, text, status=200):
        self._text = text
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, text, status=200):
        self.response = FakeResponse(text, status)

    def get(self, url, **_):
        return self.response


class ContractTests(unittest.TestCase):
    def test_contract_fixture(self):
        for case in CONTRACT['valid']:
            self.assertEqual(validate_snapshot(case['snapshot']), [], case['why'])
        for case in CONTRACT['invalid']:
            self.assertNotEqual(validate_snapshot(case['snapshot']), [], case['why'])

    def test_reserved_commands_match_the_handler(self):
        commands = {name[3:] for name in dir(TTPRaceHandler) if name.startswith('ex_')}
        presets = set(SEED_PRESETS) | set(PRESET_ALIASES)
        self.assertEqual(sorted(commands - presets), sorted(CONTRACT['reservedCommands']))


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_shortcuts_ignore_case_and_punctuation(self):
        registry = fresh(snapshot(flagset('ABNS 22 Swiss', ['abns22_swiss'], 'A' * 37)))
        self.assertEqual(registry.resolve('ABNS22_Swiss'), 'A' * 37)
        self.assertEqual(registry.resolve('abns-22-swiss'), 'A' * 37)
        self.assertEqual(reduce('ABNS22_Swiss'), 'abns22swiss')

    def test_built_in_list_only_when_no_copy_has_loaded(self):
        registry = fresh()
        self.assertFalse(registry.loaded)
        self.assertEqual(registry.resolve('abns22_swiss'), SEED_PRESETS['abns22_swiss'])
        self.assertEqual(registry.resolve('TTP4RR'), SEED_PRESETS['ttp4rp'])
        self.assertEqual(registry.resolve('corto34'), None)
        self.assertTrue(registry.is_registered(SEED_PRESETS['abns22_swiss']))
        self.assertTrue(registry.is_registered(VANILLA))
        self.assertIsNone(registry.name_of(SEED_PRESETS['abns22_swiss']))

    def test_a_deleted_shortcut_stays_deleted_once_a_copy_has_loaded(self):
        registry = fresh(snapshot())
        self.assertIn('tc33', SEED_PRESETS)
        self.assertIsNone(registry.resolve('tc33'))
        self.assertFalse(registry.is_shortcut('tc33'))
        self.assertFalse(registry.is_registered(SEED_PRESETS['tc33']))

    def test_name_of_finds_any_version(self):
        registry = fresh(snapshot())
        self.assertEqual(registry.name_of(OLD_CONSTERNATION), 'Consternation')
        self.assertEqual(registry.resolve('consternation'), CONSTERNATION)
        self.assertTrue(registry.is_registered(OLD_CONSTERNATION))

    async def test_a_good_file_is_taken_and_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'flagsets.json'
            registry = fresh(cache_path=cache)
            await registry.refresh(FakeSession(json.dumps(snapshot())))
            self.assertEqual(registry.resolve('corto34'), TC34)
            self.assertEqual(json.loads(cache.read_text(encoding='utf-8'))['revision'], 1)
            self.assertEqual([path.name for path in Path(folder).iterdir()], ['flagsets.json'])

            after_restart = fresh(cache_path=cache)
            after_restart.load_cache()
            self.assertEqual(after_restart.resolve('corto34'), TC34)

    async def test_a_bad_file_keeps_the_previous_copy_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'flagsets.json'
            registry = fresh(cache_path=cache)
            good = json.dumps(snapshot())
            await registry.refresh(FakeSession(good))
            for bad, status in [(json.dumps({'schemaVersion': 1, 'revision': 99, 'flagsets': [{}]}), 200), ('not json', 200), (good, 503)]:
                with self.assertRaises(Exception):
                    await registry.refresh(FakeSession(bad, status))
            self.assertEqual(registry.resolve('corto34'), TC34)
            self.assertEqual(cache.read_text(encoding='utf-8'), good)

    def test_a_bad_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'flagsets.json'
            cache.write_text('{"schemaVersion":1,"revision":99,"flagsets":[{}]}', encoding='utf-8')
            registry = fresh(cache_path=cache)
            registry.load_cache()
            self.assertFalse(registry.loaded)

    async def test_a_slow_refresh_does_not_hold_a_command(self):
        started = asyncio.Event()
        never = asyncio.Event()

        class Hanging(FakeResponse):
            async def __aenter__(self):
                started.set()
                await never.wait()

        class HangingSession:
            def get(self, url, **_):
                return Hanging('')

        registry = fresh(snapshot())
        refresh = asyncio.create_task(registry.refresh(HangingSession()))
        await started.wait()
        handler = command_handler()
        with use(registry):
            await asyncio.wait_for(handler.ex_summary(['tc34'], {}), timeout=1)
        self.assertTrue(handler.messages)
        refresh.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await refresh


class SummaryHeadingTests(unittest.IsolatedAsyncioTestCase):
    def test_summary_heading_uses_the_registry_name(self):
        registry = fresh(snapshot())
        with use(registry):
            self.assertTrue(format_summary(TC34)[0].startswith('Flags (Torneo Corto #34)'))
            self.assertTrue(format_summary(OLD_CONSTERNATION)[0].startswith('Flags (Consternation)'))
            self.assertTrue(format_summary(UNREGISTERED)[0].startswith('Flags'))
            self.assertFalse(format_summary(UNREGISTERED)[0].startswith('Flags ('))

    def test_no_copy_keeps_todays_heading(self):
        with use(fresh()):
            self.assertTrue(format_summary(SEED_PRESETS['tc34'])[0].startswith('Flags (tc34)'))

    async def test_a_rename_reaches_a_rolled_rooms_summary(self):
        before = snapshot()
        registry = fresh(before)
        handler = command_handler()
        handler.data['info_bot'] = f'Flags: {TC34} Seed: 12'
        renamed = json.loads(json.dumps(before))
        renamed['flagsets'][1]['name'] = 'TC34 Remastered'
        registry.install(renamed)
        with use(registry):
            await handler.ex_summary([], {})
        self.assertTrue(handler.messages[0].startswith('Flags (TC34 Remastered)'))


class HandlerWithoutSahasrahBot(unittest.IsolatedAsyncioTestCase):
    async def run_command(self, text, registry=None):
        handler = command_handler()
        handler.state = {}
        with use(registry or fresh(snapshot())), patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.chat_message({'message': {'message': text, 'message_plain': text, 'user': {'name': 'Racer'}}})
        return handler

    def rolled(self, handler, flags):
        self.assertTrue(handler.seed_rolled, handler.messages)
        self.assertIn(flags, handler.race_info_updates[-1])

    async def test_a_preset_rolls_the_latest(self):
        self.rolled(await self.run_command('!consternation'), CONSTERNATION)

    async def test_a_new_shortcut_rolls_with_no_method(self):
        self.assertFalse(hasattr(TTPRaceHandler, 'ex_tc35'))
        self.rolled(await self.run_command('!tc35'), TC35)

    async def test_an_existing_command_rolls_the_latest(self):
        self.rolled(await self.run_command('!ttp5muffle'), MUFFLE)

    async def test_ttp5_picks_from_the_three(self):
        handler = await self.run_command('!ttp5')
        self.assertTrue(handler.seed_rolled)
        self.assertTrue(any(flags in handler.race_info_updates[-1] for flags in (MUFFLE, UPHILL, PICK5)))

    async def test_race_takes_a_shortcut_in_any_case(self):
        self.rolled(await self.run_command('!race CORTO34'), TC34)

    async def test_race_refuses_an_unknown_word(self):
        handler = await self.run_command('!race nothing')
        self.assertFalse(handler.seed_rolled)
        self.assertIn('Unknown preset', handler.messages[0])
        self.assertIn('z1rracing.com/flagsets', handler.messages[0])

    async def test_a_deleted_command_says_so(self):
        handler = await self.run_command('!tc33')
        self.assertFalse(handler.seed_rolled)
        self.assertIn('tc33', handler.messages[0])

    async def test_summary_describes_the_latest(self):
        for word in ('tc34', 'corto34'):
            handler = await self.run_command(f'!summary {word}')
            self.assertTrue(handler.messages[0].startswith('Flags (Torneo Corto #34)'), handler.messages)

    async def test_summary_takes_a_name_as_one_word(self):
        handler = await self.run_command('!summary 2026ttp5mufflerug')
        self.assertTrue(handler.messages[0].startswith('Flags (TTP5 Muffle Rug)'), handler.messages)

    async def test_race_and_summary_of_a_league_week_follow_the_latest(self):
        self.rolled(await self.run_command('!race leagueweek1'), NEW_WISDOM)
        handler = await self.run_command('!summary leagueweek1')
        self.assertTrue(handler.messages[0].startswith('Flags (TTP3 Wisdom)'))

    async def test_flags_refuses_a_shortcut_and_points_at_the_command(self):
        handler = await self.run_command('!flags consternation')
        self.assertFalse(handler.seed_rolled)
        self.assertEqual(handler.race_info_updates, [])
        self.assertIn('!consternation', handler.messages[0])

    async def test_flags_refuses_short_typos(self):
        for word in ('Corto35', 'TTP5mufle'):
            handler = await self.run_command(f'!flags {word}')
            self.assertFalse(handler.seed_rolled, word)
            self.assertEqual(handler.race_info_updates, [], word)

    async def test_flags_rolls_a_registered_short_string(self):
        self.rolled(await self.run_command(f'!flags {VANILLA}'), VANILLA)

    async def test_flags_rolls_a_long_unregistered_string(self):
        self.assertEqual(len(UNREGISTERED), 37)
        self.rolled(await self.run_command(f'!flags {UNREGISTERED}'), UNREGISTERED)

    async def test_league_week_command_stays_fixed(self):
        self.rolled(await self.run_command('!leagueweek1'), LEAGUE_WEEKS[1][1])


class HandlerWithSahasrahBot(unittest.IsolatedAsyncioTestCase):
    async def run_command(self, text):
        handler = command_handler()
        handler.state = {}
        handler.sahasrahbot_present = True
        with use(fresh(snapshot())), patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.chat_message({'message': {'message': text, 'message_plain': text, 'user': {'name': 'Racer'}}})
        return handler

    async def test_a_preset_hands_off_the_latest(self):
        handler = await self.run_command('!consternation')
        self.assertFalse(handler.seed_rolled)
        self.assertIn(f'!flags {CONSTERNATION}', handler.messages[0])
        self.assertFalse(handler.messages[0].startswith('!'))
        self.assertTrue(handler.messages[0].startswith('Consternation flags: '))

    async def test_summary_still_answers(self):
        handler = await self.run_command('!summary tc34')
        self.assertTrue(handler.messages[0].startswith('Flags (Torneo Corto #34)'))

    async def test_flags_stays_silent(self):
        handler = await self.run_command('!flags tc34')
        self.assertEqual(handler.messages, [])

    async def test_league_week_hands_off_its_fixed_string(self):
        handler = await self.run_command('!leagueweek1')
        self.assertIn(f'!flags {LEAGUE_WEEKS[1][1]}', handler.messages[0])


if __name__ == '__main__':
    unittest.main()
