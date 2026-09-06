import logging
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from ttpbot.config import TIMEZONE
from ttpbot.league.roster import Racer, Roster
from ttpbot.league.booth import BoothOutcome
from ttpbot.league.matchups import Fixture
from ttpbot.league.schedule import LeagueRace
from ttpbot.league.scheduler import (
    LEAGUE_ROOM_OPEN_MINUTES_BEFORE,
    LeagueScheduler,
    ScheduleSource,
)
from ttpbot.state import UNCERTAIN_RACE, DestinationStateStore

DESTINATION = 'https://racetime.gg|z1r'
ROOM = 'https://racetime.gg/z1r/clever-slug-1234'
START = datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE)

QUIET = logging.getLogger('test-league-scheduler')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


def _racer(name, rid, did):
    return Racer(sheet_name=name, team='SC', team_full='Shadow Cartel',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id=rid, discord_id=did)


RACE = LeagueRace(start=START,
                  runner_one=_racer('SirLinkalot', 'rt-sir', '111'),
                  runner_two=_racer('Windfox470', 'rt-wind', '222'),
                  channel=None)
OTHER = LeagueRace(start=START,
                   runner_one=_racer('seanfreston', 'rt-sean', '333'),
                   runner_two=_racer('Stags28', 'rt-stags', '444'),
                   channel=None)


class FakeSource:
    def __init__(self, races):
        self._races = races
        self.calls = 0

    async def races(self, now):
        self.calls += 1
        return list(self._races)


class FakeBot:
    def __init__(self):
        self.state = {}
        self.access_token = 'token'
        self.provider = object()


class LeagueSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.created = DestinationStateStore(
            'league_races.json', DESTINATION, 'league_created_races', data_dir=root)
        self.webhooks = DestinationStateStore(
            'league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)
        self.bot = FakeBot()

    def scheduler(self, races=(RACE,)):
        return LeagueScheduler(
            bot=self.bot, source=FakeSource(list(races)),
            created_store=self.created, webhook_store=self.webhooks,
            webhook_url='https://discord.com/api/webhooks/1/token',
            logger=QUIET,
        )

    async def test_opens_a_room_inside_the_window(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create, \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=LEAGUE_ROOM_OPEN_MINUTES_BEFORE))

        create.assert_awaited_once()
        self.assertEqual(self.created.load()[RACE.key], ROOM)

    async def test_does_not_open_a_room_too_early(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create:
            await scheduler.tick(START - timedelta(minutes=45))

        create.assert_not_awaited()
        self.assertEqual(self.created.load(), {})

    async def test_does_not_reopen_a_room_on_the_next_tick(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create, \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))
            await scheduler.tick(START - timedelta(minutes=29))

        self.assertEqual(create.await_count, 1)

    async def test_opens_both_rooms_for_two_races_in_one_slot(self):
        scheduler = self.scheduler([RACE, OTHER])
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create, \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(create.await_count, 2)
        self.assertEqual(len(self.created.load()), 2)

    async def test_seeds_handler_state_with_both_racers(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(
            self.bot.state['z1r/clever-slug-1234']['league_race']['invite'],
            ['rt-sir', 'rt-wind'],
        )

    async def test_announces_once(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)) as announce:
            await scheduler.tick(START - timedelta(minutes=30))
            await scheduler.tick(START - timedelta(minutes=29))

        self.assertEqual(announce.await_count, 1)
        self.assertIn(RACE.key, self.webhooks.load())

    async def test_uncertain_creation_is_recorded_and_not_announced(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=UNCERTAIN_RACE)), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)) as announce:
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(self.created.load()[RACE.key], UNCERTAIN_RACE)
        announce.assert_not_awaited()

    async def test_failed_creation_writes_no_state(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(self.created.load(), {})

    async def test_a_race_error_does_not_stop_the_other_race(self):
        scheduler = self.scheduler([RACE, OTHER])
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(side_effect=[RuntimeError('boom'), ROOM])), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(len(self.created.load()), 1)


class FakeHttpResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return self._body


VALID_CSV = (
    'Date,Time,Game,Runner 1,Runner 2,,Comms,Tracker,,Channel,Booth\n'
    '9/2/2026,9:00:00 PM,2,(SC) Droois,(F451) Rhinohero,,,,,,\n'
)
# A 200 response whose body is Google's HTML sign-in page rather than the
# sheet: every line has no commas, so it parses to zero races without
# raising an error.
HTML_SIGNIN_PAGE = '<!doctype html>\n<html>\n<body>Sign in required</body>\n</html>\n'


def _schedule_roster():
    return Roster([
        _racer('Droois', 'rt-droois', '1'),
        _racer('Rhinohero', 'rt-rhino', '2'),
    ])


class ScheduleSourceStaleParseTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_previous_snapshot_when_a_fetch_parses_to_zero_races(self):
        logger = Mock()
        source = ScheduleSource('https://example.com/s.csv', _schedule_roster(), logger)
        good_at = datetime(2026, 9, 1, 12, 0, tzinfo=TIMEZONE)
        bad_at = good_at + timedelta(minutes=10)

        with patch('ttpbot.league.scheduler.aiohttp.request',
                   return_value=FakeHttpResponse(200, VALID_CSV)):
            first = await source.races(good_at)
        self.assertEqual(len(first), 1)

        with patch('ttpbot.league.scheduler.aiohttp.request',
                   return_value=FakeHttpResponse(200, HTML_SIGNIN_PAGE)):
            second = await source.races(bad_at)

        # The good snapshot is still served, unchanged.
        self.assertEqual(second, first)
        # The staleness clock did not advance, so SCHEDULE_CACHE_MAX_AGE
        # still counts from the last genuinely successful parse.
        self.assertEqual(source._fetched_at, good_at)
        logger.error.assert_called_once()
        self.assertIn(1, logger.error.call_args.args)

    async def test_a_genuinely_empty_sheet_with_no_prior_races_stays_empty(self):
        logger = Mock()
        source = ScheduleSource('https://example.com/s.csv', _schedule_roster(), logger)
        now = datetime(2026, 9, 1, 12, 0, tzinfo=TIMEZONE)

        with patch('ttpbot.league.scheduler.aiohttp.request',
                   return_value=FakeHttpResponse(200, HTML_SIGNIN_PAGE)):
            result = await source.races(now)

        self.assertEqual(result, [])
        self.assertEqual(source._fetched_at, now)
        logger.error.assert_not_called()


if __name__ == '__main__':
    unittest.main()


def _rival(name, rid, did):
    return Racer(sheet_name=name, team='BM', team_full='Bow Mode',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id=rid, discord_id=did)


STAGED_RACE = LeagueRace(
    start=START,
    runner_one=_racer('SirLinkalot', 'rt-sir', '111'),
    runner_two=_rival('Windfox470', 'rt-wind', '222'),
    channel='Z1Rracing',
    comms=('SirLinkalot',),
    tracker='Windfox470',
    game=1,
    # Bow Mode away at Shadow Cartel, so runner_two takes slot 1.
    fixture=Fixture(week=1, away='Bow Mode', home='Shadow Cartel'),
)


class FakeCrew:
    """Records lookups so the T-35 ordering can be asserted.

    tick() also runs the ordinary interval refresh, so a raw count cannot
    tell the forced post-wake lookup from it. The order list can.
    """

    def __init__(self, order=None):
        self.refreshes = 0
        self.order = order if order is not None else []

    async def refresh(self, url, token):
        self.refreshes += 1
        self.order.append('crew')
        return True

    def user_id_for(self, name):
        return 'u-' + name.lower()

    def mentions(self, names):
        return ['<@1>' for _ in names if _], ['1']


class WakePhaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.created = DestinationStateStore(
            'league_races.json', DESTINATION, 'league_created_races', data_dir=root)
        self.webhooks = DestinationStateStore(
            'league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)
        self.order = []
        self.crew = FakeCrew(self.order)

    def scheduler(self, races):
        return LeagueScheduler(
            bot=FakeBot(), source=FakeSource(list(races)),
            created_store=self.created, webhook_store=self.webhooks,
            webhook_url='https://discord.com/api/webhooks/1/token',
            logger=QUIET, crew=self.crew,
            roster_url='https://cp.example', roster_token='tok',
            relay_wake_url='http://127.0.0.1:3005', relay_wake_token='ci-tok',
        )

    async def test_does_not_wake_for_a_race_nobody_will_restream(self):
        scheduler = self.scheduler([RACE])
        with patch('ttpbot.league.scheduler.wake_control_plane', AsyncMock(return_value=True)) as wake:
            await scheduler.tick(START - timedelta(minutes=35))

        # Most League rows have no Channel and must not wake anything.
        wake.assert_not_awaited()

    async def test_wakes_then_forces_a_crew_lookup(self):
        scheduler = self.scheduler([STAGED_RACE])
        wake = AsyncMock(side_effect=lambda *a, **k: self.order.append('wake') or True)
        with patch('ttpbot.league.scheduler.wake_control_plane', wake), \
             patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=35))

        wake.assert_awaited_once()
        # A crew lookup follows the wake, on top of whatever the ordinary
        # interval refresh did. Without it, a lookup that failed at T-40
        # against a sleeping control plane would not retry until after T-30.
        self.assertEqual(self.order[-1], 'crew')
        self.assertIn('wake', self.order)

    async def test_skips_the_crew_lookup_when_the_wake_failed(self):
        scheduler = self.scheduler([STAGED_RACE])
        with patch('ttpbot.league.scheduler.wake_control_plane',
                   AsyncMock(side_effect=OSError('relay down'))), \
             patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=35))

        # No forced lookup follows a failed wake: the control plane is not
        # reachable, so it would only fail too.
        self.assertNotIn('wake', self.order)

    async def test_retries_a_failed_wake_on_a_later_tick(self):
        scheduler = self.scheduler([STAGED_RACE])
        wake = AsyncMock(side_effect=[OSError('relay down'), True])
        with patch('ttpbot.league.scheduler.wake_control_plane', wake), \
             patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=35))
            await scheduler.tick(START - timedelta(minutes=34))

        # /api/wake is idempotent, so retrying across the window costs nothing
        # and covers a relay that was briefly down.
        self.assertEqual(wake.await_count, 2)
        # Exactly one forced lookup, after the wake that finally worked.
        self.assertEqual(self.order[-1], 'crew')

    async def test_does_not_wake_again_once_it_worked(self):
        scheduler = self.scheduler([STAGED_RACE])
        wake = AsyncMock(return_value=True)
        with patch('ttpbot.league.scheduler.wake_control_plane', wake), \
             patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=35))
            await scheduler.tick(START - timedelta(minutes=34))

        self.assertEqual(wake.await_count, 1)

    async def test_a_wake_that_explodes_does_not_stop_the_tick(self):
        scheduler = self.scheduler([STAGED_RACE])
        with patch('ttpbot.league.scheduler.wake_control_plane',
                   AsyncMock(side_effect=RuntimeError('unexpected'))), \
             patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create, \
             patch('ttpbot.league.scheduler.request_booth', AsyncMock(return_value=BoothOutcome())), \
             patch('ttpbot.league.scheduler.send_league_announcement', AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))

        # Phase 1 carries on regardless.
        create.assert_awaited_once()


class BoothPhaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.created = DestinationStateStore(
            'league_races.json', DESTINATION, 'league_created_races', data_dir=root)
        self.webhooks = DestinationStateStore(
            'league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)

    def scheduler(self, races=(STAGED_RACE,)):
        return LeagueScheduler(
            bot=FakeBot(), source=FakeSource(list(races)),
            created_store=self.created, webhook_store=self.webhooks,
            webhook_url='https://discord.com/api/webhooks/1/token',
            logger=QUIET, crew=FakeCrew(),
            booth_url='https://cp.example', booth_token='tok',
        )

    async def test_calls_the_booth_before_announcing(self):
        order = []
        booth = AsyncMock(side_effect=lambda *a, **k: order.append('booth') or BoothOutcome('staged', 'b1'))
        announce = AsyncMock(side_effect=lambda *a, **k: order.append('announce') or True)
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(side_effect=lambda *a, **k: order.append('room') or ROOM)), \
             patch('ttpbot.league.scheduler.request_booth', booth), \
             patch('ttpbot.league.scheduler.send_league_announcement', announce):
            await scheduler.tick(START - timedelta(minutes=30))

        # The announcement is last because the endpoint's answer is what it
        # says: only the control plane knows this is a continuation.
        self.assertEqual(order, ['room', 'booth', 'announce'])

    async def test_a_continuation_reaches_the_announcement(self):
        announce = AsyncMock(return_value=True)
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.request_booth',
                   AsyncMock(return_value=BoothOutcome('continuation', 'b0'))), \
             patch('ttpbot.league.scheduler.send_league_announcement', announce):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertTrue(announce.await_args.kwargs['continuation'])

    async def test_a_staged_booth_announces_normally(self):
        announce = AsyncMock(return_value=True)
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.request_booth',
                   AsyncMock(return_value=BoothOutcome('staged', 'b1'))), \
             patch('ttpbot.league.scheduler.send_league_announcement', announce):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertFalse(announce.await_args.kwargs['continuation'])

    async def test_announces_anyway_when_the_booth_call_fails(self):
        announce = AsyncMock(return_value=True)
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.request_booth',
                   AsyncMock(side_effect=OSError('control plane unreachable'))), \
             patch('ttpbot.league.scheduler.send_league_announcement', announce):
            await scheduler.tick(START - timedelta(minutes=30))

        # Phase 2 must never be able to take Phase 1 down with it.
        announce.assert_awaited_once()
        self.assertFalse(announce.await_args.kwargs['continuation'])

    async def test_announces_for_a_race_that_gets_no_booth_at_all(self):
        announce = AsyncMock(return_value=True)
        booth = AsyncMock(return_value=BoothOutcome())
        scheduler = self.scheduler(races=(RACE,))
        with patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.request_booth', booth), \
             patch('ttpbot.league.scheduler.send_league_announcement', announce):
            await scheduler.tick(START - timedelta(minutes=30))

        # No channel, so no booth is even attempted - but the room still gets
        # its announcement, as it does today.
        booth.assert_not_awaited()
        announce.assert_awaited_once()


class WakeGuardTests(unittest.IsolatedAsyncioTestCase):
    """The wake must not fire for races that are over."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.created = DestinationStateStore(
            'league_races.json', DESTINATION, 'league_created_races', data_dir=root)
        self.webhooks = DestinationStateStore(
            'league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)

    def scheduler(self):
        return LeagueScheduler(
            bot=FakeBot(), source=FakeSource([STAGED_RACE]),
            created_store=self.created, webhook_store=self.webhooks,
            webhook_url='https://discord.com/api/webhooks/1/token',
            logger=QUIET, crew=FakeCrew(),
            roster_url='https://cp.example', roster_token='tok',
            relay_wake_url='http://127.0.0.1:3005', relay_wake_token='ci-tok',
        )

    async def test_does_not_wake_for_a_race_that_already_happened(self):
        scheduler = self.scheduler()
        wake = AsyncMock(return_value=True)
        with patch('ttpbot.league.scheduler.wake_control_plane', wake):
            # A month past. The bot restarts and re-reads the whole sheet.
            await scheduler.tick(START + timedelta(days=30))

        # Waking production for a race that finished weeks ago is real money
        # for nothing, on every restart.
        wake.assert_not_awaited()

    async def test_still_wakes_inside_the_window(self):
        scheduler = self.scheduler()
        wake = AsyncMock(return_value=True)
        with patch('ttpbot.league.scheduler.wake_control_plane', wake), \
             patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=35))

        wake.assert_awaited_once()


class ForcedCrewRefreshTests(unittest.IsolatedAsyncioTestCase):
    """A refresh that returns False has not happened."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.created = DestinationStateStore(
            'league_races.json', DESTINATION, 'league_created_races', data_dir=root)
        self.webhooks = DestinationStateStore(
            'league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)

    def scheduler(self, crew):
        return LeagueScheduler(
            bot=FakeBot(), source=FakeSource([STAGED_RACE]),
            created_store=self.created, webhook_store=self.webhooks,
            webhook_url='https://discord.com/api/webhooks/1/token',
            logger=QUIET, crew=crew,
            roster_url='https://cp.example', roster_token='tok',
            relay_wake_url='http://127.0.0.1:3005', relay_wake_token='ci-tok',
        )

    async def test_retries_when_the_forced_lookup_reported_failure(self):
        class RefusingCrew(FakeCrew):
            async def refresh(self, url, token):
                self.refreshes += 1
                # CrewDirectory returns False rather than raising, so a
                # failure looks exactly like a success to a careless caller.
                return False

        crew = RefusingCrew()
        scheduler = self.scheduler(crew)
        wake = AsyncMock(return_value=True)
        with patch('ttpbot.league.scheduler.wake_control_plane', wake), \
             patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=35))
            await scheduler.tick(START - timedelta(minutes=34))

        # Not marked done, so the next tick tries again.
        self.assertEqual(wake.await_count, 2)


class BoothRetryTests(unittest.IsolatedAsyncioTestCase):
    """An unknown booth outcome is retried; the announcement still goes once."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.created = DestinationStateStore(
            'league_races.json', DESTINATION, 'league_created_races', data_dir=root)
        self.webhooks = DestinationStateStore(
            'league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)

    def scheduler(self):
        return LeagueScheduler(
            bot=FakeBot(), source=FakeSource([STAGED_RACE]),
            created_store=self.created, webhook_store=self.webhooks,
            webhook_url='https://discord.com/api/webhooks/1/token',
            logger=QUIET, crew=FakeCrew(),
            booth_url='https://cp.example', booth_token='tok',
        )

    async def test_retries_a_booth_whose_outcome_is_unknown(self):
        scheduler = self.scheduler()
        booth = AsyncMock(side_effect=[BoothOutcome(), BoothOutcome('staged', 'b1')])
        announce = AsyncMock(return_value=True)
        with patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.request_booth', booth), \
             patch('ttpbot.league.scheduler.send_league_announcement', announce):
            await scheduler.tick(START - timedelta(minutes=30))
            await scheduler.tick(START - timedelta(minutes=29))

        # Persisting `announced` after an unknown outcome used to end the
        # tick early forever, so the booth was never built.
        self.assertEqual(booth.await_count, 2)
        # The announcement still goes exactly once - Phase 1 is not spammed.
        announce.assert_awaited_once()

    async def test_stops_calling_the_booth_once_it_answered(self):
        scheduler = self.scheduler()
        booth = AsyncMock(return_value=BoothOutcome('staged', 'b1'))
        with patch('ttpbot.league.scheduler.create_league_room', AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.request_booth', booth), \
             patch('ttpbot.league.scheduler.send_league_announcement', AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))
            await scheduler.tick(START - timedelta(minutes=29))

        self.assertEqual(booth.await_count, 1)
