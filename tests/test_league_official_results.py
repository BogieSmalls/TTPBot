import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from ttpbot.state import DestinationStateStore
from ttpbot.league.result_context import bind_room
from ttpbot.league.results import ResultsRecorder
from tests.test_league_results import ROOMS, ROSTER


class OfficialResultsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = folder.name
        self.contexts = DestinationStateStore('context.json', 'https://racetime.gg|z1r',
                                             'league_room_context', data_dir=self.root)
        self.results = DestinationStateStore('results.json', 'https://racetime.gg|z1r',
                                            'league_results', data_dir=self.root)
        self.context = {'week': 3, 'game': 2, 'scheduledAt': '2026-09-18T23:00:00+00:00',
                        'pairs': [['ISUMatt', 'Merks'], ['Sigil', 'Thomjay']]}
        self.contexts.save({'obedient-rope-9691': self.context})
        self.sender = AsyncMock(return_value={'status': 'pending'})

    def recorder(self, **kw):
        return ResultsRecorder(roster=ROSTER, store=self.results, logger=MagicMock(),
            archives_url='unused', schedule_url='unused', context_store=self.contexts,
            result_url='http://127.0.0.1:3005/internal/league/results', result_token='test',
            result_sender=self.sender, **kw)

    async def test_confirmation_only_after_official_readback_and_restart_retry(self):
        self.assertEqual(await self.recorder().record(ROOMS[0]), 0)
        self.assertEqual(self.results.load(), {})
        sent = self.sender.call_args.args[0]
        self.assertEqual((sent['week'], sent['game']), (3, 2))
        self.assertEqual(len(sent['submissions']), 2)
        self.sender.return_value = {'status': 'confirmed'}
        self.assertEqual(await self.recorder().record(ROOMS[0]), 2)
        self.assertEqual(len(self.results.load()), 2)
        self.assertEqual(await self.recorder().record(ROOMS[0]), 0)
        self.assertEqual(self.sender.await_count, 2)

    async def test_network_failure_and_restart_are_retried_from_remembered_room(self):
        self.sender.side_effect = TimeoutError()
        self.assertEqual(await self.recorder().record(ROOMS[0]), 0)
        self.sender.side_effect = None
        self.sender.return_value = {'status': 'confirmed'}
        await self.recorder(room_reader=AsyncMock(return_value=ROOMS[0])).reconcile()
        self.assertEqual(len(self.results.load()), 2)

    async def test_unreadable_room_is_not_filed_from_stale_finished_data(self):
        reader = AsyncMock(side_effect=TimeoutError())
        self.assertEqual(await self.recorder(room_reader=reader).record(ROOMS[0]), 0)
        self.sender.assert_not_awaited()

    async def test_manual_room_is_sent_for_review_without_guessing_its_game(self):
        from tests.test_league_results import ARCHIVE_ROWS
        self.contexts.save({})
        recorder=self.recorder(fetcher=AsyncMock(return_value=ARCHIVE_ROWS))
        self.assertEqual(await recorder.record(ROOMS[0]),0)
        sent=self.sender.call_args.args[0]
        self.assertTrue(sent['unresolved'])
        self.assertNotIn('game',sent)
        self.assertEqual(len(sent['submissions']),2)

    def test_room_binding_survives_restart_and_rejects_a_changed_game(self):
        self.contexts.save({})
        race = SimpleNamespace(fixture=SimpleNamespace(week=6), game=2,
            start=datetime(2026,10,10,22,tzinfo=timezone.utc),
            runner_one=ROSTER.resolve('ISUMatt'), runner_two=ROSTER.resolve('Merks'))
        bind_room(self.contexts, race, 'https://racetime.gg/z1r/test-room-1234')
        restarted = DestinationStateStore('context.json','https://racetime.gg|z1r',
                                         'league_room_context',data_dir=self.root)
        self.assertEqual(restarted.load()['test-room-1234']['game'],2)
        race.runner_one,race.runner_two=race.runner_two,race.runner_one
        race.start=datetime(2026,10,11,22,tzinfo=timezone.utc)
        bind_room(restarted,race,'https://racetime.gg/z1r/test-room-1234')
        self.assertEqual(restarted.load()['test-room-1234']['scheduledAt'],'2026-10-10T22:00:00+00:00')
        race.game = 1
        with self.assertRaisesRegex(ValueError,'identity changed'):
            bind_room(restarted,race,'https://racetime.gg/z1r/test-room-1234')
        self.assertEqual(restarted.load()['test-room-1234']['game'],2)

    def test_championship_series_games_bind_through_game_five(self):
        # 2026-10-26: Week 8 Game 1 opened its room, then failed to bind it on
        # every tick (weeks stopped at 7), so no booth and no announcement.
        # The final is a best-of-five: Games 3-5 must bind as well.
        self.contexts.save({})
        rows = (SimpleNamespace(runner_one=ROSTER.resolve('ISUMatt'), runner_two=ROSTER.resolve('Merks')),
                SimpleNamespace(runner_one=ROSTER.resolve('Sigil'), runner_two=ROSTER.resolve('Thomjay')))
        for game in range(1, 6):
            race = SimpleNamespace(fixture=SimpleNamespace(week=8), game=game, rows=rows,
                start=datetime(2026,10,27,2,tzinfo=timezone.utc))
            bind_room(self.contexts, race, 'https://racetime.gg/z1r/series-room-%d' % game)
        saved = self.contexts.load()
        self.assertEqual([saved['series-room-%d' % g]['game'] for g in range(1, 6)], [1, 2, 3, 4, 5])
        self.assertEqual(saved['series-room-5']['week'], 8)
        race.game = 6
        with self.assertRaisesRegex(ValueError, 'Game 1 to 5'):
            bind_room(self.contexts, race, 'https://racetime.gg/z1r/series-room-6')
