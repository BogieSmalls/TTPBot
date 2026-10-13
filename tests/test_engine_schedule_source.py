import copy
from datetime import datetime, timezone
import logging
import unittest
from unittest.mock import AsyncMock

from ttpbot.autumn.engine_source import EngineScheduleSource
from ttpbot.autumn.scheduler import AutumnScheduler

NOW = datetime(2026, 10, 12, 23, 35, tzinfo=timezone.utc)
AT = '2026-10-13T00:00:00Z'
DOCUMENT = {'version': 2, 'edition': '34', 'scheduleAuthority': 'engine', 'results': {}, 'games': {}, 'rooms': {},
            'gameTimes': {'W1-1': {'1': {'at': AT, 'status': 'scheduled', 'revision': 3}}}}
DRAW = {'drawn': True, 'edition': '34', 'workflowVersion': 2, 'matches': [{'id': 'W1-1', 'a': 'A', 'b': 'B', 'state': 'ready'}]}
SNAPSHOT = {'environment': 'test', 'games': [{'id': 'corto:34:W1-1:1', 'editionId': 'corto:34', 'matchId': 'W1-1', 'gameNumber': 1,
            'participants': [{'name': 'A'}, {'name': 'B'}], 'crew': {'comms1': {'name': 'Commentator'}}, 'bookings': [{'channel': 'Z1Rracing2'}]}]}


class EngineSourceTests(unittest.IsolatedAsyncioTestCase):
    def source(self, snapshot=None):
        self.fetch = AsyncMock(return_value=copy.deepcopy(SNAPSHOT if snapshot is None else snapshot))
        return EngineScheduleSource(event='corto', edition='34', url='http://private', token='reader', environment='test', logger=logging.getLogger('test'), fetch=self.fetch)

    async def test_explicit_match_game_and_database_crew_survive_reschedule(self):
        source = self.source()
        first = await source.rows(NOW, document=DOCUMENT, drawn=DRAW)
        self.assertTrue(first.readable)
        row = first.rows[0]
        self.assertEqual((row.match_id, row.game, row.comms_one, row.channel), ('W1-1', 1, 'Commentator', 'Z1Rracing2'))
        moved = copy.deepcopy(DOCUMENT)
        moved['gameTimes']['W1-1']['1']['at'] = '2026-10-13T00:30:00Z'
        again = await source.rows(NOW, document=moved, drawn=DRAW)
        self.assertEqual(again.rows[0].comms_one, row.comms_one)
        self.assertEqual(again.rows[0].at.minute, 30)
        finished = copy.deepcopy(moved); finished['results']['W1-1'] = 'A'
        self.assertEqual((await source.rows(NOW, document=finished, drawn=DRAW)).rows, [])

    async def test_old_sheet_authority_or_wrong_edition_never_falls_back(self):
        source = self.source()
        for field, value in [('scheduleAuthority', 'sheet'), ('edition', '33')]:
            document = dict(DOCUMENT, **{field: value})
            self.assertFalse((await source.rows(NOW, document=document, drawn=DRAW)).readable)
        self.fetch.assert_not_awaited()

    async def test_unavailable_crew_preserves_engine_schedule_without_reusing_booking(self):
        source = self.source()
        await source.rows(NOW, document=DOCUMENT, drawn=DRAW)
        self.fetch.side_effect = TimeoutError('no owner')
        schedule = await source.rows(NOW, document=DOCUMENT, drawn=DRAW)
        self.assertTrue(schedule.readable)
        self.assertEqual(len(schedule.rows), 1)
        row = schedule.rows[0]
        self.assertEqual((row.match_id, row.game, row.runner_one, row.runner_two), ('W1-1', 1, 'A', 'B'))
        self.assertFalse(row.crew_available)
        self.assertEqual((row.channel, row.crew), ('', []))

    async def test_bad_crew_binding_cannot_hide_a_valid_engine_schedule(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['games'][0]['participants'][0]['name'] = 'Someone else'
        schedule = await self.source(snapshot).rows(NOW, document=DOCUMENT, drawn=DRAW)
        self.assertEqual(len(schedule.rows), 1)
        self.assertFalse(schedule.rows[0].crew_available)
        self.assertEqual(schedule.rows[0].channel, '')
        self.assertTrue(schedule.bad)

    async def test_data_outage_allows_room_and_invites_holds_booth_then_recovers(self):
        source = self.source()
        self.fetch.side_effect = TimeoutError('no owner')
        engine = type('Engine', (), {'edition': '34'})()
        engine.draw = AsyncMock(return_value=DRAW)
        engine.state = AsyncMock(return_value={'document': DOCUMENT})
        booth = AsyncMock()
        scheduler = AutumnScheduler(source, engine, logging.getLogger('test'), event='corto', request_booth=booth)
        scheduler._room_work = AsyncMock(return_value={'mayOpen': True, 'mayWake': True})
        scheduler._room_v2 = AsyncMock(return_value='https://racetime.gg/z1r/test-room')
        scheduler._wake_booth = AsyncMock()
        scheduler._let_in = AsyncMock()
        scheduler._tell = AsyncMock()
        await scheduler.tick(NOW)
        scheduler._room_v2.assert_awaited_once()
        scheduler._let_in.assert_awaited_once()
        scheduler._wake_booth.assert_not_awaited()
        booth.assert_not_awaited()
        self.fetch.side_effect = None
        await scheduler.tick(NOW)
        scheduler._wake_booth.assert_awaited_once()
        booth.assert_awaited_once()
        self.assertEqual(booth.await_args.args[1].channel, 'Z1Rracing2')

    async def test_scheduler_opens_from_engine_without_mirroring_or_missing_row_cancellation(self):
        engine = type('Engine', (), {'edition': '34'})()
        engine.draw = AsyncMock(return_value=DRAW)
        engine.state = AsyncMock(return_value={'document': DOCUMENT})
        engine.mirror_time = AsyncMock(side_effect=AssertionError('a migrated tournament must not mirror'))
        engine.cancel_time = AsyncMock(side_effect=AssertionError('missing rows cannot cancel engine schedules'))
        scheduler = AutumnScheduler(self.source(), engine, logging.getLogger('test'), event='corto')
        scheduler._room_work = AsyncMock(return_value={'mayOpen': True, 'mayWake': False})
        scheduler._room_v2 = AsyncMock(return_value=None)
        await scheduler.tick(NOW)
        scheduler._room_v2.assert_awaited_once()
        race, row, work = scheduler._room_v2.await_args.args
        self.assertEqual(race.identity.key, 'corto|34|W1-1|1')
        self.assertEqual(row.comms_one, 'Commentator')
        engine.mirror_time.assert_not_awaited(); engine.cancel_time.assert_not_awaited()

    async def test_retired_sheet_source_is_not_even_read_after_engine_authority_switch(self):
        source = type('Sheet', (), {'configured': True})()
        source.rows = AsyncMock(side_effect=AssertionError('retired Sheet read'))
        engine = type('Engine', (), {'edition': '34'})()
        engine.draw = AsyncMock(return_value=DRAW); engine.state = AsyncMock(return_value={'document': DOCUMENT})
        await AutumnScheduler(source, engine, logging.getLogger('test'), event='corto').tick(NOW)
        source.rows.assert_not_awaited()
