import copy
import logging
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ttpbot.autumn.engine import Written, RECORDED, UNCONFIRMED
from ttpbot.autumn.results import AutumnResults, command_for
from ttpbot.provider import RacetimeProvider
from ttpbot.state import DestinationStateStore, StateStoreError

ROOM = 'https://racetime.gg/z1r/result-one'
RACE = SimpleNamespace(match_id='GF-1', runner_one='Alice', runner_two='Bob',
                       identity=SimpleNamespace(game=1))
DATA = {'name': 'z1r/result-one', 'status': {'value': 'finished'},
        'ended_at': '2026-10-02T23:30:00Z', 'entrants': [
            {'user': {'id': 'a'}, 'status': {'value': 'done'}, 'finish_time': 'PT1H0M0.101S'},
            {'user': {'id': 'b'}, 'status': {'value': 'done'}, 'finish_time': 'PT1H0M0.102S'}]}

class AutumnResultsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provider = RacetimeProvider('https://racetime.gg', 'z1r')
        self.store = DestinationStateStore('autumn_results.json', self.provider.destination_key,
                                          'autumn_results', data_dir=self.temp.name)
        self.engine = SimpleNamespace(state=AsyncMock(return_value={'document': {
            'racetimeIds': {'Alice': 'a', 'Bob': 'b'}, 'results': {}, 'games': {}}}),
            draw=AsyncMock(return_value={'matches': [{'id': 'GF-1', 'a': 'Alice', 'b': 'Bob'}]}))
        self.reader = AsyncMock(return_value=copy.deepcopy(DATA))
        self.recorder = self.make()

    def make(self):
        return AutumnResults(store=self.store, engine=self.engine, provider=self.provider,
                             logger=logging.getLogger('test'), reader=self.reader,
                             event='autumn', edition='2026')

    async def test_restart_recovers_finished_room_and_preserves_exact_game_identity(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        restarted = self.make()
        await restarted.recover()
        entry = self.store.load()['autumn-2026|GF-1|1']
        self.assertEqual(entry['status'], 'suggested')
        self.assertEqual(entry['winner'], 'Alice')
        self.assertIn('--game 1', command_for('autumn-2026|GF-1|1', entry, event='autumn'))
        self.assertNotIn('--commit', command_for('autumn-2026|GF-1|1', entry, event='autumn'))
        before = self.store.path.read_bytes()
        await restarted.record(DATA)
        self.assertEqual(self.store.path.read_bytes(), before)
        # Resolving the currently ready reset must never reassign this receipt.
        reset = SimpleNamespace(**{**vars(RACE), 'match_id': 'GF-2'})
        with self.assertRaises(StateStoreError):
            restarted.bind(reset, ROOM, {'Alice': 'a', 'Bob': 'b'})

    async def test_one_finisher_beats_dnf_in_either_entrant_order(self):
        for finisher in (0, 1):
            with self.subTest(finisher=finisher):
                self.store.save({})
                self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
                data = copy.deepcopy(DATA)
                data['entrants'][1-finisher].update(status={'value': 'dnf'}, finish_time=None)
                await self.recorder.record(data)
                entry = self.store.load()['autumn-2026|GF-1|1']
                self.assertEqual(entry['status'], 'suggested')
                self.assertEqual(entry['winner'], ('Alice', 'Bob')[finisher])

    async def test_dnf_needs_a_valid_finisher_and_finished_room(self):
        for change in ('double-dnf', 'missing-time', 'still-racing', 'cancelled'):
            with self.subTest(change=change):
                self.store.save({})
                self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
                data = copy.deepcopy(DATA)
                data['entrants'][1].update(status={'value': 'dnf'}, finish_time=None)
                if change == 'double-dnf': data['entrants'][0].update(status={'value': 'dnf'}, finish_time=None)
                if change == 'missing-time': data['entrants'][0]['finish_time'] = None
                if change == 'still-racing': data['entrants'][1]['status']['value'] = 'in_progress'
                if change == 'cancelled': data['status']['value'] = 'cancelled'
                await self.recorder.record(data)
                entry = self.store.load()['autumn-2026|GF-1|1']
                self.assertEqual(entry['status'], 'review')
                self.assertIsNone(entry['winner'])

    async def test_bad_ids_ties_and_nonfinishes_need_human_review(self):
        for change in ('wrong-id', 'tie', 'dnf', 'dq', 'extra', 'missing', 'cancelled'):
            with self.subTest(change=change):
                self.store.save({})
                self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
                data = copy.deepcopy(DATA)
                if change == 'wrong-id': data['entrants'][0]['user']['id'] = 'someone-else'
                if change == 'tie': data['entrants'][1]['finish_time'] = data['entrants'][0]['finish_time']
                if change in ('dnf', 'dq'): data['entrants'][1]['status']['value'] = change
                if change == 'extra': data['entrants'].append(copy.deepcopy(data['entrants'][0]))
                if change == 'missing': data['entrants'].pop()
                if change == 'cancelled': data['status']['value'] = 'cancelled'
                await self.recorder.record(data)
                entry = self.store.load()['autumn-2026|GF-1|1']
                self.assertEqual(entry['status'], 'review')
                self.assertIsNone(entry['winner'])

    async def test_result_already_recorded_is_not_another_suggestion(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        self.engine.state.return_value['document']['games'] = {'GF-1': [{'game': 1, 'winner': 'Alice', 'room': ROOM}]}
        await self.recorder.record(DATA)
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['status'], 'recorded')

    async def test_unknown_room_and_foreign_room_never_reach_engine(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        await self.recorder.record(dict(DATA, name='z1r/other-room'))
        self.engine.state.assert_not_awaited()
        with self.assertRaises(Exception):
            self.recorder.bind(RACE, 'https://evil.example/z1r/result-one', {'Alice': 'a', 'Bob': 'b'})

    async def test_failed_read_keeps_tracking_and_can_recover_later(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        self.reader.side_effect = TimeoutError('no answer')
        await self.recorder.recover()
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['status'], 'tracking')
        self.reader.side_effect = None
        await self.recorder.recover()
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['status'], 'suggested')

    def test_corrupt_receipts_remain_blocked_after_restart(self):
        self.store.path.write_text('broken')
        with self.assertRaises(StateStoreError): self.store.load()
        with self.assertRaises(StateStoreError): self.make().bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})

    async def test_changed_bracket_pair_does_not_get_a_winner_suggestion(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        self.engine.draw.return_value['matches'][0]['b'] = 'Chris'
        await self.recorder.record(DATA)
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['status'], 'review')

    async def test_operator_acceptance_clears_suggestion_on_next_poll(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        await self.recorder.record(DATA)
        self.engine.state.return_value['document']['games'] = {'GF-1': [{'game': 1, 'winner': 'Alice', 'room': ROOM}]}
        await self.recorder.recover()
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['status'], 'recorded')

    async def test_live_handler_finish_uses_the_saved_receipt(self):
        from ttpbot.handler import TTPRaceHandler
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        handler = TTPRaceHandler(conn=None, logger=logging.getLogger('test'), state={})
        handler.data = copy.deepcopy(DATA)
        handler.autumn_room = True
        handler.autumn_results = self.recorder
        await handler.end()
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['winner'], 'Alice')

    async def test_recovery_rejects_an_answer_for_a_different_room(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        self.reader.return_value = dict(DATA, name='z1r/other-room')
        await self.recorder.recover()
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['status'], 'tracking')


    async def test_observation_is_saved_before_delivery_and_replayed_after_restart(self):
        self.engine.state.return_value['document'].update(version=2, edition='2026')
        self.engine.bind_result_room = AsyncMock(return_value=Written(RECORDED))
        saved_ids = set()
        async def deliver(facts):
            entry = self.store.load()['autumn-2026|GF-1|1']
            self.assertEqual(entry['observations'][facts['observationId']]['facts'], facts)
            saved_ids.add(facts['observationId'])
            return Written(UNCONFIRMED)
        self.engine.observe_result = AsyncMock(side_effect=deliver)
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        await self.recorder.record(DATA)
        entry = self.store.load()['autumn-2026|GF-1|1']
        self.assertIn('observations', entry)
        self.assertIsNone(next(iter(entry['observations'].values()))['receipt'])
        self.engine.observe_result.side_effect = lambda facts: Written(RECORDED, answer={'observation': {'id': facts['observationId'], 'proposalId': 'proposal-one'}})
        await self.make().recover()
        entry = self.store.load()['autumn-2026|GF-1|1']
        self.assertEqual(next(iter(entry['observations'].values()))['receipt']['proposalId'], 'proposal-one')
        self.assertEqual(saved_ids, set(entry['observations']))
        self.assertEqual(self.engine.observe_result.await_count, 2)
        await self.make().recover()
        self.assertEqual(self.engine.observe_result.await_count, 2)

    async def test_same_winner_in_a_different_room_does_not_clear_a_suggestion(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        await self.recorder.record(DATA)
        self.engine.state.return_value['document']['games'] = {'GF-1': [{'game': 1, 'winner': 'Alice', 'room': 'https://racetime.gg/z1r/another-room'}]}
        await self.recorder.recover()
        self.assertEqual(self.store.load()['autumn-2026|GF-1|1']['status'], 'review')

    async def test_changed_finish_preserves_both_observations_and_holds_the_local_suggestion(self):
        self.recorder.bind(RACE, ROOM, {'Alice': 'a', 'Bob': 'b'})
        await self.recorder.record(DATA)
        changed = copy.deepcopy(DATA)
        changed['entrants'][1]['finish_time'] = 'PT59M'
        await self.recorder.record(changed)
        entry = self.store.load()['autumn-2026|GF-1|1']
        self.assertEqual(entry['status'], 'review')
        self.assertEqual(len(entry['observations']), 2)

    async def test_real_engine_accepts_finisher_over_dnf_after_a_lost_reply_and_restart(self):
        import asyncio
        import json
        import os
        from pathlib import Path
        import subprocess
        from ttpbot.autumn.engine import AutumnEngine
        engine_root = os.environ.get('Z1RR_ENGINE_DIR')
        if not engine_root:
            self.skipTest('set Z1RR_ENGINE_DIR for the real-engine contract test')
        root = Path(engine_root).resolve()
        code = '''
import { createEngineService } from SERVICE;
import { createTournamentStore } from STORE;
const server = createEngineService({ token: 'scratch-token', storeFor: event => createTournamentStore({ event, dir: DIR }) });
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
'''.replace('SERVICE', json.dumps((root / 'src/service.mjs').as_uri())).replace('STORE', json.dumps((root / 'src/store.mjs').as_uri())).replace('DIR', json.dumps(str(Path(self.temp.name) / 'engine')))
        process = await asyncio.create_subprocess_exec('node', '--input-type=module', '--eval', code,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        async def stop():
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
        self.addAsyncCleanup(stop)
        port = int(await asyncio.wait_for(process.stdout.readline(), 10))
        client = AutumnEngine(url='http://127.0.0.1:{}'.format(port), token='scratch-token')
        names = ['Alice'] + ['Racer{}'.format(i) for i in range(2, 16)] + ['Bob']
        for operation, payload in [('draw', {'seeds': names}), ('racetime', {'racetimeIds': {'Alice': 'Za', 'Bob': 'aZ'}}), ('migrate', {'edition': '2026'})]:
            self.assertTrue((await client._post(operation, payload)).ok)
        self.engine = client
        self.recorder = self.make()
        race = SimpleNamespace(**{**vars(RACE), 'match_id': 'W1-1'})
        self.recorder.bind(race, ROOM, {'Alice': 'Za', 'Bob': 'aZ'})
        original = client.observe_result
        async def lose_reply(facts):
            self.assertTrue((await original(facts)).ok)
            return Written(UNCONFIRMED)
        client.observe_result = lose_reply
        mixed_ids = copy.deepcopy(DATA)
        mixed_ids['entrants'][0]['user']['id'] = 'Za'
        mixed_ids['entrants'][1]['user']['id'] = 'aZ'
        mixed_ids['entrants'][1].update(status={'value': 'dnf'}, finish_time=None)
        from ttpbot.handler import TTPRaceHandler
        handler = TTPRaceHandler(conn=None, logger=logging.getLogger('test'), state={})
        handler.data, handler.autumn_room, handler.autumn_results = mixed_ids, True, self.recorder
        await handler.end()
        state = (await client.state())['document']
        self.assertEqual(len(state['proposals']), 1)
        self.assertEqual(state['results'], {})
        client.observe_result = original
        await self.make().recover()
        state = (await client.state())['document']
        self.assertEqual(len(state['proposals']), 1)
        item = next(iter(self.store.load()['autumn-2026|W1-1|1']['observations'].values()))
        self.assertIsNotNone(item['receipt'])
        proposal = next(iter(state['proposals'].values()))
        self.assertEqual(proposal['status'], 'pending')
        self.assertEqual(proposal['facts']['winner'], 'Alice')
        self.assertIn('DNF', proposal['facts']['reason'])
        self.assertTrue((await client._post('decideResult', dict(edition='2026', proposalId=proposal['id'],
            proposalRevision=proposal['revision'], decision='confirm', decisionId='council', actor='council'))).ok)
        state = (await client.state())['document']
        self.assertEqual(state['results']['W1-1'], 'Alice')
        self.assertEqual(len(state['actions']), 1)
