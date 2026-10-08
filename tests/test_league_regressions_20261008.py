"""Two League failures from the night of 2026-10-07.

Bogie vs Antlerz44 (Week 6): Antlerz44 was briefly finished at 1:07:11, undid it
and forfeited, so the room finished twice and the results form got two
contradictory rows. And every late-room check raised "HTTP URL path is
invalid", so a late crew could never get an open League room on air.
"""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ttpbot.league.results import ResultsRecorder
from ttpbot.league.scheduler import LeagueScheduler
from ttpbot.provider import RacetimeProvider
from ttpbot.state import DestinationStateStore
from tests.test_league_results import ARCHIVE_ROWS, ROOMS, ROSTER, SCHEDULE_HEADER


class _Response:
    def __init__(self, body):
        self.status = 200
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class LateRoomCheck(unittest.IsolatedAsyncioTestCase):
    async def test_the_late_room_check_asks_a_path_the_provider_accepts(self):
        bot = SimpleNamespace(provider=RacetimeProvider('https://racetime.gg', 'z1r'))
        scheduler = LeagueScheduler(bot=bot, source=None, created_store=MagicMock(), webhook_store=MagicMock(),
                                    webhook_url='https://discord.com/api/webhooks/1/token', logger=MagicMock())
        asked = []
        def request(method, url, timeout):
            asked.append(url)
            return _Response({'status': {'value': 'in_progress'}})
        with patch('ttpbot.league.scheduler.aiohttp.request', side_effect=request):
            active = await scheduler._room_active('https://racetime.gg/z1r/agreeable-triforce-6446')
        self.assertTrue(active)
        self.assertEqual(asked, ['https://racetime.gg/z1r/agreeable-triforce-6446/data'])
        scheduler.logger.warning.assert_not_called()


class ResultSettles(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = DestinationStateStore('league_results.json', 'https://racetime.gg|z1r', 'league_results', data_dir=self._dir.name)
        self.posted = []

    def recorder(self, room):
        async def requester(url, data):
            self.posted.append(data)
            return True
        async def fetcher(url):
            return ARCHIVE_ROWS if 'gid=1495655076' in url else [SCHEDULE_HEADER]
        async def reread(race_data):
            return room['now']
        return ResultsRecorder(roster=ROSTER, store=self.store, logger=MagicMock(),
                               archives_url='https://sheet/export?gid=1495655076',
                               schedule_url='https://sheet/export?gid=2033319762',
                               requester=requester, fetcher=fetcher,
                               settle_seconds=0.05, room_reader=reread)

    async def test_a_finish_undone_inside_the_wait_files_once_from_the_final_room(self):
        first = ROOMS[0]
        final = dict(first, ended_at='2026-10-08T04:04:41.636Z')
        room = {'now': first}
        recorder = self.recorder(room)
        one = asyncio.create_task(recorder.record(first))      # the room finishes...
        await asyncio.sleep(0.01)
        room['now'] = final                                    # ...is undone, and finishes again
        two = asyncio.create_task(recorder.record(final))
        sent = await one + await two
        self.assertEqual(sent, len(self.posted))
        self.assertEqual(len(self.posted), 2, 'one submission per pairing, once')
        keys = list(self.store.load())
        self.assertTrue(all('2026-10-08T04:04:41.636' in key for key in keys), 'filed from the settled room')

    async def test_a_room_already_filed_is_not_filed_again_under_a_new_end_time(self):
        room = {'now': ROOMS[0]}
        recorder = self.recorder(room)
        await recorder.record(ROOMS[0])
        self.posted.clear()
        later = dict(ROOMS[0], ended_at='2026-10-08T09:00:00.000Z')
        room['now'] = later
        self.assertEqual(await recorder.record(later), 0)
        self.assertEqual(self.posted, [])
        recorder.logger.warning.assert_called()


if __name__ == '__main__':
    unittest.main()
