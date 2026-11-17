"""Zora mode: a TTPBot process serving ZORA's own category.

It joins every open room there, unlisted ones included, starts the ZORA preset
feed and nothing of the z1r category's, and refuses to start with a z1r feature
switched on. A z1r process is untouched by any of it.
"""

import asyncio
import logging
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ttpbot.bot import TTPBot
from ttpbot.provider import RacetimeProvider

LOG = logging.getLogger('zora-mode-test')
ROOT = Path(__file__).resolve().parents[1]


def room(name='zora/clever-link-1234', status='open', unlisted=False):
    return {'name': name, 'status': {'value': status}, 'goal': {'name': 'Beat the game'},
            'category': {'slug': name.split('/')[0]}, 'unlisted': unlisted, 'info_bot': '', 'info_user': ''}


def bot(category='zora'):
    instance = TTPBot.__new__(TTPBot)
    instance.provider = RacetimeProvider('https://racetime.gg', category)
    instance.logger = LOG
    instance.handlers = {}
    instance.state = {}
    instance.access_token = 'token'
    return instance


class Handles(unittest.TestCase):
    def test_every_open_zora_room_is_handled(self):
        zora = bot()
        self.assertTrue(zora.should_handle(room()))
        self.assertTrue(zora.should_handle(room(unlisted=True)))
        self.assertFalse(zora.should_handle(room(status='finished')))
        self.assertFalse(zora.should_handle(room(status='cancelled')))

    def test_z1r_still_handles_only_its_own_rooms(self):
        z1r = bot('z1r')
        self.assertFalse(z1r.should_handle(room('z1r/clever-link-1234')))
        self.assertFalse(z1r._zora_mode())


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self):
        return self.body


class Lists(unittest.IsolatedAsyncioTestCase):
    async def names(self, answers):
        calls = []

        def request(method, url, headers=None, timeout=None):
            calls.append((url, dict(headers or {})))
            answer = answers[url]
            if isinstance(answer, Exception):
                raise answer
            return FakeResponse(*answer)

        with patch('ttpbot.bot.aiohttp.request', side_effect=request):
            return await bot()._category_room_names(), calls

    async def test_unlisted_rooms_are_listed_with_the_bots_token(self):
        names, calls = await self.names({
            'https://racetime.gg/o/zora/data': (200, {'current_races': [{'name': 'zora/a'}, {'name': 'zora/b'}]}),
        })
        self.assertEqual(names, ['zora/a', 'zora/b'])
        self.assertEqual(calls, [('https://racetime.gg/o/zora/data', {'Authorization': 'Bearer token'})])

    async def test_the_public_list_when_the_bot_list_fails(self):
        for failure in ((403, {}), OSError('down')):
            names, calls = await self.names({
                'https://racetime.gg/o/zora/data': failure,
                'https://racetime.gg/zora/data': (200, {'current_races': [{'name': 'zora/a'}]}),
            })
            self.assertEqual(names, ['zora/a'])
            self.assertEqual(calls[-1], ('https://racetime.gg/zora/data', {}), 'no token on the public list')

    async def test_nothing_when_both_fail(self):
        names, _ = await self.names({'https://racetime.gg/o/zora/data': (500, {}),
                                     'https://racetime.gg/zora/data': OSError('down')})
        self.assertEqual(names, [])


class Joins(unittest.IsolatedAsyncioTestCase):
    async def test_a_room_is_joined_once(self):
        zora = bot()
        zora.loop = asyncio.get_running_loop()
        zora._category_room_names = AsyncMock(return_value=['zora/a'])
        zora._read_room = AsyncMock(return_value=room('zora/a', unlisted=True))
        zora.create_handler = MagicMock(return_value=SimpleNamespace(handle=AsyncMock()))
        await zora._join_category_rooms_once()
        await zora._join_category_rooms_once()
        zora._read_room.assert_awaited_once_with('zora/a', label='Zora mode')
        zora.create_handler.assert_called_once()
        self.assertIn('zora/a', zora.handlers)

    async def test_a_finished_or_unreadable_room_is_not_joined(self):
        zora = bot()
        zora.loop = asyncio.get_running_loop()
        zora._category_room_names = AsyncMock(return_value=['zora/a'])
        zora.create_handler = MagicMock()
        zora._read_room = AsyncMock(return_value=room('zora/a', status='finished'))
        await zora._join_category_rooms_once()
        zora._read_room = AsyncMock(return_value=None)
        await zora._join_category_rooms_once()
        zora.create_handler.assert_not_called()
        self.assertEqual(zora.handlers, {})


class Startup(unittest.TestCase):
    def started(self, category):
        instance = TTPBot.__new__(TTPBot)
        instance.provider = RacetimeProvider('https://racetime.gg', category)
        instance.logger = LOG
        instance.league_enabled = True
        names = []

        def create_task(coro):
            names.append(getattr(coro, '__qualname__', 'mock'))
            if hasattr(coro, 'close') and asyncio.iscoroutine(coro):
                coro.close()

        instance.loop = SimpleNamespace(create_task=create_task, set_exception_handler=lambda handler: None,
                                        run_forever=lambda: None)
        instance.ttp_runner = None
        for builder in ('_build_league_scheduler', '_build_nightly_booths', '_build_autumn_runner',
                        '_build_corto_runner', '_build_ttp_runner'):
            setattr(instance, builder, MagicMock(return_value=None))
        zora_registry = MagicMock()
        zora_registry.run = MagicMock(return_value='zora presets')
        with patch.dict('os.environ', {}, clear=True), patch('ttpbot.bot.season_record'), \
                patch('ttpbot.bot.flagset_registry'), patch('ttpbot.bot.zora_registry', zora_registry):
            instance.run()
        return instance, names, zora_registry

    def test_zora_runs_its_presets_and_rooms_only(self):
        instance, names, zora_registry = self.started('zora')
        self.assertIn('TTPBot.join_category_rooms', names)
        zora_registry.run.assert_called_once()
        for z1r_task in ('TTPBot.race_scheduler', 'Bot.refresh_races', 'TTPBot.join_own_rooms'):
            self.assertNotIn(z1r_task, names)
        for builder in ('_build_league_scheduler', '_build_nightly_booths', '_build_autumn_runner',
                        '_build_corto_runner', '_build_ttp_runner'):
            getattr(instance, builder).assert_not_called()

    def test_z1r_starts_exactly_as_before(self):
        instance, names, zora_registry = self.started('z1r')
        self.assertIn('TTPBot.race_scheduler', names)
        self.assertIn('Bot.refresh_races', names)
        self.assertNotIn('TTPBot.join_category_rooms', names)
        zora_registry.run.assert_not_called()
        instance._build_league_scheduler.assert_called_once()


class CheckConfig(unittest.TestCase):
    """`python -m ttpbot --check-config`, as the preflight runs it."""

    def check(self, **env):
        base = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT),
                'TTPBOT_RACETIME_ORIGIN': 'https://racetime.gg', 'TTPBOT_RACETIME_CLIENT_ID': 'id',
                'TTPBOT_RACETIME_CLIENT_SECRET': 'secret', 'TTPBOT_ENVIRONMENT': 'production'}
        return subprocess.run([sys.executable, '-m', 'ttpbot', '--check-config'], capture_output=True,
                              text=True, cwd=ROOT, env={**base, **env})

    def test_a_plain_zora_process_passes(self):
        result = self.check(TTPBOT_CATEGORY_SLUG='zora', TTPBOT_DATA_DIR='/var/lib/ttpbot-zora')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         'TTPBOT_CONFIG=PASS destination=https://racetime.gg|zora league=off')

    def test_a_zora_process_with_a_z1r_feature_is_refused(self):
        for key, value in (('TTPBOT_LEAGUE_ENABLED', 'true'), ('Z1RR_TTP_ENABLED', 'true'),
                           ('TTPBOT_DATA_DIR', '/var/lib/ttpbot')):
            env = {'TTPBOT_CATEGORY_SLUG': 'zora', 'TTPBOT_DATA_DIR': '/var/lib/ttpbot-zora', key: value}
            result = self.check(**env)
            self.assertEqual(result.returncode, 2, key)
            self.assertIn('zora mode refused', result.stderr)

    def test_z1r_with_its_features_passes_as_before(self):
        result = self.check(TTPBOT_CATEGORY_SLUG='z1r', TTPBOT_DATA_DIR='/var/lib/ttpbot',
                            TTPBOT_LEAGUE_ENABLED='true', Z1RR_TTP_ENABLED='true')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'TTPBOT_CONFIG=PASS destination=https://racetime.gg|z1r league=on')


if __name__ == '__main__':
    unittest.main()
