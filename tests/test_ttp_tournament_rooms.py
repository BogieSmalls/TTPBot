"""TTP Season 5 tournament rooms, opened by the tournament runner.

The runner that opens Autumn and Torneo Corto rooms opens these too. What is
TTP's own is the name -- the season's `tournament.roomLabel` -- the round names,
and what the handler is told about the race, which picks build on (Task 18).
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ttpbot import room_policy
from ttpbot.autumn.announce import build_announcement
from ttpbot.autumn.matching import RaceIdentity, Resolved
from ttpbot.autumn.rooms import autumn_room_form_data, recover_autumn_room, room_title
from ttpbot.autumn.wiring import build_autumn_runner, round_label
from ttpbot.bot import TTPBot
from ttpbot.handler import TTPRaceHandler
from ttpbot.provider import RacetimeProvider

LOG = logging.getLogger('ttp-tournament-test')
START = datetime(2027, 1, 9, 1, 0, tzinfo=timezone.utc)
ENV = {'Z1RR_ENGINE_TOKEN': 'scratch', 'Z1RR_TTP_ENABLED': 'true', 'Z1RR_TTP_EDITION': '5',
       'COMPETITIONS_URL': 'http://private', 'COMPETITIONS_READER_TOKEN': 'reader',
       'COMPETITIONS_ENVIRONMENT': 'test'}
TITLE = 'TTP Season 5 Tournament — Round 1 — thomjay vs specialk3782 [W1-25] Game 2'


def ttp_race(match_id='W1-25', game=2, rounds=6):
    race = Resolved(identity=RaceIdentity('ttp', match_id, game, '5'), match_id=match_id,
                    at=START, runner_one='thomjay', runner_two='specialk3782')
    race.best_of = 3
    race.rounds = rounds
    return race


def room(info_user='', info_bot='', goal='Beat the game'):
    return {'goal': {'name': goal}, 'info_user': info_user, 'info_bot': info_bot}


class Naming(unittest.TestCase):
    def test_a_game_two_room_is_named_for_the_season_round_and_game(self):
        race = ttp_race()
        label = round_label(race)
        self.assertEqual(label, 'Round 1')
        self.assertEqual(room_title(race, label), TITLE)
        form = autumn_room_form_data(race, label)
        self.assertEqual(form['goal'], 'Beat the game')
        self.assertEqual(form['info_user'], TITLE)

    def test_the_last_round_is_the_championship(self):
        race = ttp_race('W6-1', 1)
        self.assertEqual(round_label(race), 'Championship')
        self.assertEqual(room_title(race, round_label(race)),
                         'TTP Season 5 Tournament — Championship — thomjay vs specialk3782 [W6-1] Game 1')

    def test_autumn_labels_are_unchanged(self):
        race = Resolved(identity=RaceIdentity('autumn', 'W2-3', 1, '2026'), match_id='W2-3',
                        at=START, runner_one='a', runner_two='b')
        self.assertIsNone(round_label(race))
        self.assertEqual(round_label(Resolved(identity=RaceIdentity('autumn', 'GF-1', 1, '2026'),
                                              match_id='GF-1', at=START, runner_one='a', runner_two='b')),
                         'Grand Final')

    def test_an_unknown_event_or_season_is_never_named_autumn(self):
        with self.assertRaises(ValueError):
            room_policy.tournament_name('nope', '1')
        with self.assertRaises(ValueError):
            room_policy.tournament_name('ttp', '9')
        self.assertEqual(room_policy.tournament_name('autumn', '2026'), '2026 Autumn Tournament')
        self.assertEqual(room_policy.tournament_name('corto', '33'), 'Torneo Corto #33')
        self.assertEqual(room_policy.tournament_name('ttp', '5'), 'TTP Season 5 Tournament')

    def test_the_discord_announcement_names_ttp_not_autumn(self):
        race = ttp_race()
        content = build_announcement(race, 'https://racetime.gg/z1r/ttp-room', label='Round 1')['content']
        self.assertTrue(content.startswith('TTP Season 5 Tournament · W1-25 — Round 1 - Game 2: '), content)
        self.assertNotIn('Autumn', content)


class Recognition(unittest.TestCase):
    def test_only_the_tournament_test_claims_a_ttp_tournament_room(self):
        for data in (room(info_user=TITLE), room(info_bot=TITLE),
                     room(info_user=TITLE + '\nRestream: https://www.twitch.tv/z1rracing')):
            with self.subTest(data=data):
                self.assertTrue(room_policy.is_ttp_tournament_room(data))
                self.assertFalse(room_policy.is_autumn_room(data))
                self.assertFalse(room_policy.is_corto_room(data))
                self.assertFalse(room_policy.is_league_room(data))
                self.assertFalse(room_policy.is_ttp_scheduled_room(data))

    def test_a_community_room_is_not_claimed(self):
        self.assertFalse(room_policy.is_ttp_tournament_room(room(info_user=TITLE, goal='Random seed')))
        self.assertFalse(room_policy.is_ttp_tournament_room(room(info_user='TTP Season 5 Tournament practice')))
        self.assertFalse(room_policy.is_ttp_tournament_room(room()))

    def test_the_bot_handles_a_ttp_tournament_room(self):
        bot = TTPBot.__new__(TTPBot)
        self.assertTrue(bot.should_handle(dict(room(info_user=TITLE), status={'value': 'open'})))


class Recovery(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_finds_the_room_under_its_title(self):
        race = ttp_race()
        race.room_marker = 'Z1RR:scratch'
        listing = {'current_races': [
            {'name': 'z1r/ttp-room', 'info_user': TITLE + '\nRestream: https://www.twitch.tv/z1rracing'},
            {'name': 'z1r/other', 'info_user': TITLE.replace('W1-25', 'W1-26')},
        ]}

        class Response:
            status = 200

            async def json(self, content_type=None):
                return listing

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        with patch('ttpbot.autumn.rooms.aiohttp.request', lambda **kw: Response()):
            found = await recover_autumn_room(race, RacetimeProvider('https://racetime.gg', 'z1r'), 'token', LOG,
                                              label='Round 1')
        self.assertEqual(found, 'https://racetime.gg/z1r/ttp-room')


class Handler(unittest.IsolatedAsyncioTestCase):
    async def test_the_handler_gets_when_and_game_when_the_seed_lands_after_begin(self):
        state = {}
        handler = TTPRaceHandler(conn=None, logger=LOG, state=state)
        handler.data = dict(room(info_user=TITLE), name='z1r/ttp-room', status={'value': 'open'}, entrants=[])
        handler.ws = SimpleNamespace(send=AsyncMock())
        handler.invite_user = AsyncMock()
        await handler.begin()
        self.assertTrue(handler.ttp_tournament_room)
        self.assertFalse(handler.autumn_room or handler.corto_room or handler.league_room)
        self.assertIsNone(handler.tournament_race())
        handler.invite_user.assert_not_awaited()

        state['autumn_race'] = {'invite': ['one', 'two'], 'title': TITLE, 'event': 'ttp', 'edition': '5',
                                'match_id': 'W1-25', 'game': 2, 'runners': ['thomjay', 'specialk3782'],
                                'when': START.isoformat()}
        await state['_autumn_send_invites']()
        self.assertEqual([c.args[0] for c in handler.invite_user.await_args_list], ['one', 'two'])
        seeded = handler.tournament_race()
        self.assertEqual(seeded['game'], 2)
        self.assertEqual(seeded['when'], START)
        self.assertEqual(seeded['match_id'], 'W1-25')
        self.assertEqual(seeded['runners'], ['thomjay', 'specialk3782'])

    async def test_a_finished_ttp_room_goes_only_to_the_ttp_collector(self):
        handler = TTPRaceHandler(conn=None, logger=LOG, state={})
        handler.ttp_tournament_room = True
        handler.data = {'name': 'z1r/ttp-room'}
        handler.ttp_results = SimpleNamespace(record=AsyncMock())
        handler.autumn_results = SimpleNamespace(record=AsyncMock())
        handler.corto_results = SimpleNamespace(record=AsyncMock())
        await handler.end()
        handler.ttp_results.record.assert_awaited_once_with(handler.data)
        handler.autumn_results.record.assert_not_awaited()
        handler.corto_results.record.assert_not_awaited()


class Switch(unittest.TestCase):
    def build(self, env):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        bot = TTPBot.__new__(TTPBot)
        bot.data_dir = root.name
        bot.provider = RacetimeProvider('https://racetime.gg', 'z1r')
        bot.logger = LOG
        bot.state = {}
        self.addCleanup(setattr, TTPRaceHandler, 'ttp_results', None)
        with patch.dict('os.environ', env, clear=True):
            return bot._build_ttp_runner()

    def test_turned_off_the_runner_does_not_start(self):
        self.assertIsNone(self.build(dict(ENV, Z1RR_TTP_ENABLED='false')))
        self.assertIsNone(TTPRaceHandler.ttp_results)
        self.assertIsNone(self.build({k: v for k, v in ENV.items() if k != 'Z1RR_TTP_ENABLED'}))

    def test_turned_on_it_keeps_its_own_state_files_and_reads_the_engine_schedule(self):
        runner = self.build(ENV)
        self.assertIsNotNone(runner)
        self.assertEqual(runner.scheduler.event, 'ttp')
        self.assertEqual(runner.engine.edition, '5')
        self.assertEqual(runner.scheduler.source.authority, 'engine')
        self.assertEqual(runner.scheduler.created_store.path.name, 'ttp_created_races.json')
        self.assertEqual(runner.results.scope, 'ttp-5')

    def test_ttp_has_no_schedule_sheet_to_fall_back_to(self):
        bot = SimpleNamespace(provider=RacetimeProvider('https://racetime.gg', 'z1r'), access_token='scratch', state={})
        with self.assertRaises(ValueError):
            build_autumn_runner(dict(ENV, Z1RR_TTP_SCHEDULE_AUTHORITY='sheet'), bot, LOG, event='ttp')


class Night(unittest.IsolatedAsyncioTestCase):
    """One game on the real engine: the room at T-30, once, and not again after a restart."""

    async def test_one_room_thirty_minutes_before_and_none_after_a_restart(self):
        from ttpbot.state import DestinationStateStore
        engine_root = os.environ.get('Z1RR_ENGINE_DIR')
        if not engine_root:
            self.skipTest('set Z1RR_ENGINE_DIR for the real engine')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(engine_root).resolve()
        clock = Path(temp.name) / 'clock.txt'
        clock.write_text((START - timedelta(minutes=40)).isoformat())
        code = '''
import {readFileSync} from 'node:fs';
import {createEngineService} from SERVICE;
import {createTournamentWriter} from WRITER;
import {createTournamentStore} from STORE;
const storeFor=(event,edition)=>createTournamentStore({event,edition,dir:DIR});
const writer=createTournamentWriter({storeFor,now:()=>new Date(readFileSync(CLOCK,'utf8'))});
const server=createEngineService({token:'scratch',storeFor,writer});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
'''
        for key, value in {'SERVICE': (root / 'src/service.mjs').as_uri(), 'WRITER': (root / 'src/writer.mjs').as_uri(),
                           'STORE': (root / 'src/store.mjs').as_uri(), 'DIR': str(Path(temp.name) / 'engine'),
                           'CLOCK': str(clock)}.items():
            code = code.replace(key, json.dumps(value))
        process = await asyncio.create_subprocess_exec(
            'node', '--input-type=module', '--eval', code, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        async def stop():
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
        self.addAsyncCleanup(stop)
        port = int(await asyncio.wait_for(process.stdout.readline(), 10))

        env = dict(ENV, Z1RR_ENGINE_URL='http://127.0.0.1:{}'.format(port))
        bot = SimpleNamespace(provider=RacetimeProvider('https://racetime.gg', 'z1r'), access_token='scratch', state={})
        stores = {kind: DestinationStateStore(kind.replace('autumn_', 'ttp_') + '.json', bot.provider.destination_key,
                                              kind, data_dir=temp.name)
                  for kind in ['autumn_bindings', 'autumn_created_races', 'autumn_mirrored_times',
                               'autumn_sent_webhooks', 'autumn_booth_notices', 'autumn_results']}
        snapshot = {'environment': 'test', 'currentEditions': {'ttp': '5'}, 'games': []}

        def runner_now():
            runner = build_autumn_runner(env, bot, LOG, stores, event='ttp')
            runner.scheduler.source._fetch = AsyncMock(return_value=snapshot)
            return runner

        runner = runner_now()
        engine = runner.engine
        names = ['thomjay', 'b2', 'c3', 'd4', 'e5', 'f6', 'g7', 'specialk3782']
        for op, body in [('draw', {'edition': '5', 'seeds': names, 'format': 'single'}),
                         ('migrate', {'edition': '5'}),
                         ('series', {'edition': '5', 'bestOf': 3}),
                         ('racetime', {'racetimeIds': {'thomjay': 'one', 'specialk3782': 'two'}})]:
            answer = await engine._post(op, body)
            self.assertTrue(answer.ok, (op, answer))
        document = (await engine.state())['document']
        answer = await engine._post('scheduleAuthority', {'edition': '5', 'authority': 'engine', 'actor': '123456789012345678',
                                                          'requestId': 'adopt', 'expectedRevision': document['revision']})
        self.assertTrue(answer.ok, answer)
        answer = await engine._post('time', {'edition': '5', 'matchId': 'W1-1', 'game': 1, 'at': START.isoformat(),
                                             'source': 'discord', 'setBy': '123456789012345678', 'requestId': 'g1',
                                             'participants': ['thomjay', 'specialk3782'], 'expectedScheduleRevision': 0})
        self.assertTrue(answer.ok, answer)

        url = 'https://racetime.gg/z1r/ttp-scratch'
        with patch('ttpbot.autumn.wiring.create_autumn_room', new_callable=AsyncMock, return_value=url) as create:
            for minutes, opened in ((31, 0), (30, 1), (29, 1)):
                now = START - timedelta(minutes=minutes)
                clock.write_text(now.isoformat())
                await runner.scheduler.tick(now)
                self.assertEqual(create.await_count, opened, minutes)
            race = create.await_args.args[0]
            self.assertEqual(create.await_args.kwargs['label'], 'Round 1')
            self.assertEqual(room_title(race, 'Round 1'),
                             'TTP Season 5 Tournament — Round 1 — thomjay vs specialk3782 [W1-1] Game 1')
            seeded = bot.state['z1r/ttp-scratch']['autumn_race']
            self.assertEqual(seeded['invite'], ['one', 'two'])
            self.assertEqual({k: seeded[k] for k in ('event', 'edition', 'match_id', 'game', 'runners', 'when')},
                             {'event': 'ttp', 'edition': '5', 'match_id': 'W1-1', 'game': 1,
                              'runners': ['thomjay', 'specialk3782'], 'when': START.isoformat()})

            again = runner_now()
            now = START - timedelta(minutes=28)
            clock.write_text(now.isoformat())
            await again.scheduler.tick(now)
            self.assertEqual(create.await_count, 1)
        self.assertEqual(len((await engine.state())['document']['rooms']), 1)


if __name__ == '__main__':
    unittest.main()
