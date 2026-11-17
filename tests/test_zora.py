"""Zora mode's network-free pieces: race info, the zora.gg link, ZORA flag
strings, the built-in presets, room policy and the start-up refusal."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from ttpbot import zora
from ttpbot.config import ZORA_PRESETS
from ttpbot.room_policy import is_zora_room

ROOT = Path(__file__).resolve().parents[1]
Z1R = '143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx'


class RaceInfoTests(unittest.TestCase):
    def test_race_info_carries_both_strings_and_a_zora_gg_link(self):
        self.assertEqual(
            zora.race_info(8368178622449674251, Z1R, '13.7Zg4ZxiL'),
            'Seed: 8368178622449674251 - Flags: 143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx - ZORA: 13.7Zg4ZxiL'
            ' - https://zora.gg/?flags=143oNtDD4PAvB%21U8nSk4mxFW0br2w5YUspe7sx&zora=13.7Zg4ZxiL'
            '&seed=8368178622449674251')

    def test_no_zora_string_reads_none_and_leaves_zora_out_of_the_link(self):
        self.assertEqual(
            zora.race_info(5, 'oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW', ''),
            'Seed: 5 - Flags: oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW - ZORA: none'
            ' - https://zora.gg/?flags=oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW&seed=5')

    def test_flags_readers_find_the_z1r_string(self):
        from ttpbot.tournament_room import current_roll
        info = zora.race_info(5, Z1R, '13.7Zg4ZxiL')
        self.assertEqual(current_roll(info), Z1R)

    def test_seed_range_fits_a_zora_gg_link(self):
        self.assertLess(zora.SEED_MAX, 2 ** 64)


class ZoraFlagStringTests(unittest.TestCase):
    def test_a_zora_string_is_version_dot_payload(self):
        for good in ('13.7Zg4ZxiL', '4.T9Jvg', '18.1DzN7gO9LZ4p', '10.J2EGOED'):
            self.assertEqual(zora.zora_flags(good), good)

    def test_underscore_reads_as_bang(self):
        self.assertEqual(zora.zora_flags('13.ab_c'), '13.ab!c')

    def test_not_a_zora_string(self):
        for bad in ('', 'oIbnPf', '0.abc', '13.', '.abc', '13.ab-c', '13.ab.c', 'v13.abc', None, 13):
            self.assertIsNone(zora.zora_flags(bad), bad)


class PresetTests(unittest.TestCase):
    def test_every_built_in_zora_string_is_a_zora_string(self):
        for key, (flags, zora_string) in ZORA_PRESETS.items():
            self.assertTrue(flags, key)
            if zora_string:
                self.assertEqual(zora.zora_flags(zora_string), zora_string, key)


class RoomPolicyTests(unittest.TestCase):
    def test_a_zora_room_by_category(self):
        self.assertTrue(is_zora_room({'name': 'zora/clever-link-1234', 'category': {'slug': 'zora'}}))
        self.assertFalse(is_zora_room({'name': 'z1r/clever-link-1234', 'category': {'slug': 'z1r'}}))

    def test_a_zora_room_by_name_when_data_has_no_category(self):
        self.assertTrue(is_zora_room({'name': 'zora/clever-link-1234'}))
        for name in ('z1r/clever-link-1234', 'z1rr/test-room', 'zoraish/room', '', None):
            self.assertFalse(is_zora_room({'name': name}), name)


def config(category='zora', data_dir='/var/lib/ttpbot-zora', webhook=None):
    return SimpleNamespace(category_slug=category, data_dir=data_dir, discord_webhook_url=webhook)


class StartupTests(unittest.TestCase):
    def test_a_plain_zora_process_may_start(self):
        self.assertEqual(zora.startup_problems({}, config()), [])
        self.assertEqual(zora.startup_problems({'TTPBOT_LEAGUE_ENABLED': 'false'}, config()), [])

    def test_z1r_is_never_refused(self):
        env = {key: 'true' for key in zora.Z1R_FEATURES}
        self.assertEqual(zora.startup_problems(env, config('z1r', '/var/lib/ttpbot', 'https://discord.com/x')), [])
        self.assertEqual(zora.startup_problems(env, config('z1rr', '/var/lib/ttpbot')), [])

    def test_each_z1r_feature_refuses_a_zora_process(self):
        for key in zora.Z1R_FEATURES:
            problems = zora.startup_problems({key: 'true'}, config())
            self.assertEqual(len(problems), 1, key)
            self.assertIn(key, problems[0])

    def test_announcements_and_z1r_state_refuse(self):
        self.assertTrue(zora.startup_problems({}, config(webhook='https://discord.com/api/webhooks/1/x')))
        self.assertTrue(zora.startup_problems({}, config(data_dir='/var/lib/ttpbot')))
        self.assertTrue(zora.startup_problems({}, config(data_dir='/var/lib/ttpbot/')))
        self.assertTrue(zora.startup_problems({}, config(data_dir='/var/lib/ttpbot/zora')))
        self.assertEqual(zora.startup_problems({}, config(data_dir='/var/lib/ttpbot-zora')), [])


class ImportTests(unittest.TestCase):
    def test_zora_imports_no_network_library(self):
        tree = ast.parse((ROOT / 'ttpbot' / 'zora.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add(node.module.split('.')[0])
        self.assertFalse(imported & {'racetime_bot', 'aiohttp', 'websockets', 'requests'})


if __name__ == '__main__':
    unittest.main()
