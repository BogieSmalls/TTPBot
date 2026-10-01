import copy
import logging
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        self.engine.state.return_value['document']['games'] = {'GF-1': [{'game': 1, 'winner': 'Alice'}]}
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
        self.engine.state.return_value['document']['games'] = {'GF-1': [{'game': 1, 'winner': 'Alice'}]}
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
