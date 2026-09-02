import logging
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from ttpbot.config import TIMEZONE
from ttpbot.league.roster import Racer, Roster
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
