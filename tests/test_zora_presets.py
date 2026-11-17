"""zora.gg's presets.json through the flag registry.

The same reader as Council's flag registry, with a ZORA flag string beside
each Z1R string. Council's contract must mean exactly the same in zora mode,
and the z1r registry must not change at all.
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path

from ttpbot.config import PRESET_ALIASES, SEED_PRESETS, ZORA_PRESETS
from ttpbot.flagset_registry import FlagsetRegistry, registry, validate_snapshot, zora_registry
from tests.test_flagset_registry import FakeSession, snapshot as council_snapshot

FIXTURES = Path(__file__).parent / 'fixtures'
COUNCIL = json.loads((FIXTURES / 'flagsets-contract.json').read_text(encoding='utf-8'))
ZORA = json.loads((FIXTURES / 'zora-presets-contract.json').read_text(encoding='utf-8'))

ZTE1 = ('143oNtDD4PAvB!U8nSk4yS8L09lgT10vRVe7sx', '10.J2EGOED')
ZTE2 = ('143oNtDD4PAvB!U8nSk4yS8L09lgT10vRVe7sx', '10.J2EkcYg')
ASNB = ('8hq4BeR1JXo7yjOOHjbxryVQW!UJ3A', '4.T9Jvg')
HOPLA = 'oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW'


def preset(name, shortcuts, flags, zora=None):
    entry = {'flags': flags, 'isLatest': True}
    if zora is not None:
        entry['zora'] = zora
    return {'name': name, 'description': None, 'shortcuts': list(shortcuts), 'strings': [entry]}


def presets(*flagsets, revision=1):
    return {'schemaVersion': 1, 'revision': revision, 'updatedAt': '2026-11-17T20:00:00.000Z',
            'flagsets': list(flagsets)}


FEED = presets(
    preset('Zora The Explorer Week 1', ['zte1'], *ZTE1),
    preset('Zora The Explorer Week 2', ['zte2'], *ZTE2),
    preset('All Swords No Boards', ['asnb'], *ASNB),
    preset('Hopla', ['hopla'], HOPLA, ''),
    preset('Vanilla', ['vanilla'], 'CEERPCkLslB6BApr1Him1SiR28Y'),
)


def zora_reader(snap=None, cache_path=None, url='https://zora.test/presets.json'):
    reader = FlagsetRegistry(url, cache_path,
                             {key: flags for key, (flags, _z) in ZORA_PRESETS.items()}, {},
                             zora_strings={key: z for key, (_f, z) in ZORA_PRESETS.items()},
                             setting='ZORA_PRESETS_URL', label='ZORA presets')
    if snap:
        reader.install(snap)
    return reader


class ContractTests(unittest.TestCase):
    def test_council_contract_means_the_same_in_zora_mode(self):
        for case in COUNCIL['valid'] + COUNCIL['invalid']:
            self.assertEqual(validate_snapshot(case['snapshot'], zora=True),
                             validate_snapshot(case['snapshot']), case['why'])

    def test_zora_contract_fixture(self):
        for case in ZORA['valid']:
            self.assertEqual(validate_snapshot(case['snapshot'], zora=True), [], case['why'])
        for case in ZORA['invalid']:
            self.assertNotEqual(validate_snapshot(case['snapshot'], zora=True), [], case['why'])

    def test_council_mode_ignores_zora_strings_as_it_does_today(self):
        # Without zora=True a `zora` key is an unknown key, which Council's
        # contract has always ignored, and uniqueness stays on the Z1R string.
        self.assertEqual(validate_snapshot(ZORA['valid'][0]['snapshot']), [])
        self.assertNotEqual(validate_snapshot(ZORA['valid'][2]['snapshot']), [])

    def test_the_feed_used_below_is_valid(self):
        self.assertEqual(validate_snapshot(FEED, zora=True), [])


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    def test_built_ins_before_a_copy_loads(self):
        reader = zora_reader()
        self.assertFalse(reader.loaded)
        self.assertEqual(reader.resolve_pair('zte5'), ZORA_PRESETS['zte5'])
        self.assertEqual(reader.resolve_pair('Consternation-NoEnc'), ZORA_PRESETS['consternation-noenc'])
        self.assertEqual(reader.resolve_pair('hopla'), (HOPLA, ''))
        self.assertTrue(reader.is_shortcut('asnb'))
        self.assertIsNone(reader.resolve_pair('ttp5'))

    def test_a_loaded_copy_replaces_the_built_ins(self):
        reader = zora_reader(FEED)
        self.assertEqual(reader.resolve_pair('zte1'), ZTE1)
        self.assertEqual(reader.resolve_pair('ZTE-2'), ZTE2)
        self.assertEqual(reader.resolve_pair('All Swords No Boards'), ASNB)
        self.assertEqual(reader.resolve_pair('vanilla'), ('CEERPCkLslB6BApr1Him1SiR28Y', ''))
        self.assertIsNone(reader.resolve_pair('zte5'), 'a preset the feed dropped stays dropped')
        self.assertFalse(reader.is_shortcut('All Swords No Boards'), 'a name is not a bare command')
        self.assertTrue(reader.is_registered(ASNB[0]))

    async def test_refresh_takes_a_valid_feed_and_caches_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'zora_presets.json'
            reader = zora_reader(cache_path=cache)
            await reader.refresh(FakeSession(json.dumps(FEED)))
            self.assertEqual(reader.resolve_pair('asnb'), ASNB)
            again = zora_reader(cache_path=cache)
            again.load_cache()
            self.assertEqual(again.resolve_pair('zte2'), ZTE2)

    async def test_refresh_refuses_an_invalid_feed_and_keeps_the_last_copy(self):
        reader = zora_reader(FEED)
        bad = presets(preset('A', ['a'], 'AAAA', 'T9Jvg'))
        with self.assertRaises(ValueError):
            await reader.refresh(FakeSession(json.dumps(bad)))
        self.assertEqual(reader.resolve_pair('asnb'), ASNB)

    async def test_a_council_reader_refuses_a_feed_that_needs_zora_mode(self):
        council = FlagsetRegistry('https://z1rracing.test/flagsets.json', None, SEED_PRESETS, PRESET_ALIASES)
        with self.assertRaises(ValueError):
            await council.refresh(FakeSession(json.dumps(FEED)))
        self.assertFalse(council.loaded)

    async def test_off_without_a_url_names_its_own_setting(self):
        with self.assertLogs('ttpbot.flagset_registry', logging.INFO) as logs:
            await zora_reader(url=None).run()
        self.assertEqual(logs.output, ['INFO:ttpbot.flagset_registry:ZORA presets off: no ZORA_PRESETS_URL'])


class Z1rRegistryUnchangedTests(unittest.IsolatedAsyncioTestCase):
    def test_the_z1r_registry_is_not_a_zora_reader(self):
        self.assertFalse(registry.zora)
        self.assertEqual((registry.setting, registry.label), ('Z1RR_FLAGSETS_URL', 'Flag registry'))
        self.assertTrue(zora_registry.zora)

    async def test_z1r_log_lines_are_unchanged(self):
        council = FlagsetRegistry(None, None, SEED_PRESETS, PRESET_ALIASES)
        with self.assertLogs('ttpbot.flagset_registry', logging.INFO) as logs:
            await council.run()
        self.assertEqual(logs.output, ['INFO:ttpbot.flagset_registry:Flag registry off: no Z1RR_FLAGSETS_URL'])

    def test_a_council_copy_resolves_as_before(self):
        council = FlagsetRegistry('https://z1rracing.test/flagsets.json', None, SEED_PRESETS, PRESET_ALIASES)
        council.install(council_snapshot())
        self.assertIsNone(council._zora_forms)
        self.assertEqual(council.resolve('tc34'), 'oIbnRjuQKp1DXiYCys9LwvSpzqZbXk1pRmZzr')


if __name__ == '__main__':
    unittest.main()
