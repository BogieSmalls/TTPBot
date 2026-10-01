import importlib
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tests.test_autumn_adapters import race, Log, ROOM_URL
from ttpbot.autumn.schedule import ScheduleRow
from ttpbot.league.booth import BoothOutcome

class AutumnBoothTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ttpbot.autumn.booths'))
        cls = importlib.import_module('ttpbot.autumn.booths').AutumnBooths
        self.race = race()
        self.row = ScheduleRow(self.race.at, 'ISUMatt', 'chessjerk', channel='Z1Rracing')
        self.engine = SimpleNamespace(
            state=AsyncMock(return_value={'document': {'twitchChannels': {'ISUMatt': 'isumatt', 'chessjerk': 'chessjerk'},
                   'racetimeIds': {'ISUMatt': 'id-one', 'chessjerk': 'id-two'}}}),
            draw=AsyncMock(return_value={'matches': [{'id': 'W1-1', 'number': 1, 'a': 'ISUMatt', 'b': 'chessjerk', 'state': 'ready'}]}))
        self.crew = SimpleNamespace(refresh=AsyncMock(return_value=True), user_id_for=lambda name: None)
        self.wake = AsyncMock()
        self.booths = cls(engine=self.engine, crew=self.crew, logger=Log(), base_url='https://cp.example', token='secret',
                          edition='2026', wake=self.wake, roster_url='https://cp.example/internal/relay/league/roster')

    async def test_preparation_refreshes_crew_after_wake_and_retries_an_unsuccessful_refresh(self):
        self.crew.refresh.return_value = False
        with self.assertRaises(RuntimeError):
            await self.booths.prepare(self.race, 'Z1Rracing')
        self.crew.refresh.return_value = True
        await self.booths.prepare(self.race, 'Z1Rracing')
        self.assertEqual(self.wake.await_count, 2)
        self.assertEqual(self.crew.refresh.await_count, 2)

    async def test_unknown_booth_retries_the_same_key_then_caches_success(self):
        with patch('ttpbot.autumn.booths.request_booth', new_callable=AsyncMock) as request:
            request.side_effect = [BoothOutcome(), BoothOutcome('staged', 'one')]
            self.assertIsNone((await self.booths.request(self.race, self.row, ROOM_URL)).outcome)
            self.assertEqual((await self.booths.request(self.race, self.row, ROOM_URL)).broadcast_id, 'one')
            self.assertEqual((await self.booths.request(self.race, self.row, ROOM_URL)).broadcast_id, 'one')
            self.assertEqual(request.await_count, 2)
            self.assertEqual(request.await_args_list[0].args[0]['requestKey'], request.await_args_list[1].args[0]['requestKey'])
            self.assertEqual(request.await_args.kwargs['endpoint'], '/internal/relay/tournament/broadcast')

    async def test_a_completed_or_replaced_match_cannot_get_a_booth(self):
        with patch('ttpbot.autumn.booths.request_booth', new_callable=AsyncMock) as request:
            self.engine.draw.return_value['matches'][0]['state'] = 'played'
            await self.booths.request(self.race, self.row, ROOM_URL)
            request.assert_not_awaited()

    async def test_existing_bot_wiring_provides_the_real_booth_adapter(self):
        from ttpbot.autumn.wiring import build_autumn_runner
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'engine', 'Z1RR_CONTROL_PLANE_URL': 'https://cp.example',
                                     'Z1RR_ROSTER_TOKEN': 'roster'}, SimpleNamespace(state={}), Log())
        self.assertTrue(callable(getattr(runner.scheduler, '_request_booth', None)))


from tests.test_autumn_scheduler import SchedulerTests, START, ROOM, at, sheet, row, ready, run
from ttpbot.autumn.announce import build_announcement
from ttpbot.league.announce import ALREADY_ON_AIR

class BoothNightTests(SchedulerTests):
    def configured_scheduler(self, outcomes, notices, initial):
        scheduler = self.scheduler(sheet(row(START, 'ISUMatt', 'chessjerk', 'z1rracing')),
                                   {'W1-1': ready('ISUMatt', 'chessjerk')})
        async def request(race, scheduled, url):
            self.assertEqual(url, ROOM)
            return outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        async def announce(race, scheduled, url, booth=None):
            initial.append(booth)
        async def notice(race, scheduled, url):
            notices.append(url)
        scheduler._request_booth = request
        scheduler._announce = announce
        scheduler._announce_continuation = notice
        scheduler.booth_notice_store = self.store('autumn_booth_notices')
        scheduler.booth_notices = scheduler.booth_notice_store.load()
        return scheduler

    def test_retry_after_announcement_and_restart_never_reopens_room_or_repeats_warning(self):
        outcomes = [BoothOutcome(), BoothOutcome('continuation', 'live')]
        notices, initial = [], []
        scheduler = self.configured_scheduler(outcomes, notices, initial)
        run(scheduler.tick(at(START, 30)))
        self.assertEqual(len(initial), 1)
        self.assertEqual(notices, [])
        run(scheduler.tick(at(START, 29)))
        self.assertEqual(notices, [ROOM])
        restarted = self.configured_scheduler(outcomes, notices, initial)
        run(restarted.tick(at(START, 28)))
        self.assertEqual(self.opened, ['W1-1'])
        self.assertEqual(notices, [ROOM])
        self.assertEqual(len(initial), 1)
        self.assertEqual(self.store('autumn_booth_notices').load(), {'autumn|W1-1': True})

    def test_immediate_continuation_warning_is_in_first_announcement(self):
        notices, initial = [], []
        scheduler = self.configured_scheduler([BoothOutcome('continuation', 'live')], notices, initial)
        run(scheduler.tick(at(START, 30)))
        run(scheduler.tick(at(START, 29)))
        self.assertTrue(initial[0].is_continuation)
        self.assertEqual(notices, [])
        self.assertEqual(len(initial), 1)

    def test_message_reports_continuation_without_another_racer_ping_on_correction(self):
        body = build_announcement(race(), ROOM, ids={'ISUMatt': '123'}, continuation=True)
        self.assertIn(ALREADY_ON_AIR, body['content'])
        correction = build_announcement(race(), ROOM, ids={'ISUMatt': '123'}, continuation=True, correction=True)
        self.assertIn('Correction', correction['content'])
        self.assertEqual(correction['allowed_mentions']['users'], [])
