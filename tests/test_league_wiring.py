"""Wiring tests: a League construction failure must never take TTP down with it.

`TTPBot._build_league_scheduler` loads the roster, builds two state stores,
and constructs a `LeagueScheduler` (which synchronously loads both stores in
its own `__init__`). Any of those steps can raise, and none of them may
escape `run()` before `self.loop.run_forever()` is reached, or the three
TTP tasks that were already `create_task`'d would never actually run.
"""

import unittest
from unittest.mock import Mock, patch

from ttpbot.bot import TTPBot
from ttpbot.league.roster import RosterError
from ttpbot.state import StateStoreError


def bot_for_league(*, league_enabled=True):
    """A TTPBot-shaped object built without running __init__ or touching I/O."""
    bot = object.__new__(TTPBot)
    bot.provider = Mock(destination_key='racetime.gg|z1r')
    bot.data_dir = '/var/lib/ttpbot'
    bot.logger = Mock()
    bot.league_enabled = league_enabled
    bot.league_schedule_url = 'https://example.com/schedule.csv'
    bot.league_discord_webhook_url = None
    return bot


class RaisingStore:
    """A DestinationStateStore stand-in whose load() always fails."""

    def load(self):
        raise StateStoreError('state document fields are invalid')


class BuildLeagueSchedulerTests(unittest.TestCase):
    def test_returns_none_and_logs_when_roster_is_unusable(self):
        bot = bot_for_league()
        with patch('ttpbot.league.roster.load_roster',
                   side_effect=RosterError('roster is malformed')):
            result = bot._build_league_scheduler()
        self.assertIsNone(result)
        bot.logger.error.assert_called()

    def test_the_scheduler_shares_the_bots_slate(self):
        from ttpbot.booth_slate import ChannelSlate
        bot = bot_for_league()
        bot.slate = ChannelSlate()
        bot.league_matchups_url = None
        bot.league_discord_bot_token = None
        bot.league_scheduling_channel_id = None
        with patch('ttpbot.league.roster.load_roster', return_value=Mock()), \
             patch('ttpbot.bot.DestinationStateStore', side_effect=lambda *a, **kw: Mock(load=Mock(return_value={}))), \
             patch('ttpbot.league.database_source.configured_source', return_value=None):
            scheduler = bot._build_league_scheduler()
        self.assertIsNotNone(scheduler, bot.logger.error.call_args)
        self.assertIs(scheduler.slate, bot.slate)

    def test_run_shares_one_slate_with_every_scheduler(self):
        bot = bot_for_league(league_enabled=False)
        bot.loop = FakeLoop()
        bot.handle_exception = Mock()
        bot.reauthorize = lambda: 'reauthorize-marker'
        bot.refresh_races = lambda: 'refresh-races-marker'
        bot.race_scheduler = lambda: 'race-scheduler-marker'
        seen = []
        bot._build_nightly_booths = lambda crew: seen.append(bot.slate)
        for builder in ('_build_autumn_runner', '_build_corto_runner', '_build_ttp_runner'):
            setattr(bot, builder, Mock(return_value=None))
        with patch('ttpbot.bot.flagset_registry.run', new=lambda: 'flagset-registry-marker'), \
             patch('ttpbot.bot.season_record.run', new=lambda: 'season-record-marker'), \
             patch('ttpbot.bot.season_record.load_cache'):
            bot.run()
        from ttpbot.booth_slate import ChannelSlate
        self.assertIsInstance(seen[0], ChannelSlate)

    def test_returns_none_and_logs_when_a_store_load_fails(self):
        bot = bot_for_league()
        with patch('ttpbot.league.roster.load_roster', return_value=Mock()), \
             patch('ttpbot.bot.DestinationStateStore',
                   side_effect=lambda *a, **kw: RaisingStore()):
            result = bot._build_league_scheduler()
        self.assertIsNone(result)
        bot.logger.error.assert_called()


class FakeLoop:
    """Records what run() schedules, without ever creating a real Task."""

    def __init__(self):
        self.tasks = []
        self.exception_handler = None
        self.run_forever_called = False

    def create_task(self, marker):
        self.tasks.append(marker)
        return marker

    def set_exception_handler(self, handler):
        self.exception_handler = handler

    def run_forever(self):
        self.run_forever_called = True


class RunReachesRunForeverTests(unittest.TestCase):
    def setUp(self):
        registry = patch('ttpbot.bot.flagset_registry.run', new=lambda: 'flagset-registry-marker')
        registry.start()
        self.addCleanup(registry.stop)
        record = patch('ttpbot.bot.season_record.run', new=lambda: 'season-record-marker')
        record.start()
        self.addCleanup(record.stop)

    def test_a_broken_league_state_store_still_lets_ttp_scheduling_start(self):
        """The constraint that actually matters: TTP is unaffected."""
        bot = bot_for_league(league_enabled=True)
        bot.loop = FakeLoop()
        bot.handle_exception = Mock()
        # Stub the three TTP task methods so create_task never receives a
        # real coroutine object (an un-awaited coroutine would raise a
        # RuntimeWarning and dirty the test output).
        bot.reauthorize = lambda: 'reauthorize-marker'
        bot.refresh_races = lambda: 'refresh-races-marker'
        bot.race_scheduler = lambda: 'race-scheduler-marker'

        with patch('ttpbot.league.roster.load_roster', return_value=Mock()), \
             patch('ttpbot.bot.DestinationStateStore',
                   side_effect=lambda *a, **kw: RaisingStore()):
            bot.run()

        self.assertTrue(bot.loop.run_forever_called)
        self.assertEqual(
            bot.loop.tasks,
            ['reauthorize-marker', 'refresh-races-marker', 'race-scheduler-marker', 'flagset-registry-marker', 'season-record-marker'],
        )
        bot.logger.error.assert_called()

    def test_run_creates_only_the_ttp_tasks_and_the_registry_when_league_is_disabled(self):
        """When league_enabled=False, run() creates the three TTP tasks and the registry."""
        bot = bot_for_league(league_enabled=False)
        bot.loop = FakeLoop()
        bot.handle_exception = Mock()
        bot.reauthorize = lambda: 'reauthorize-marker'
        bot.refresh_races = lambda: 'refresh-races-marker'
        bot.race_scheduler = lambda: 'race-scheduler-marker'
        bot._build_league_scheduler = Mock(return_value=Mock())

        bot.run()

        self.assertTrue(bot.loop.run_forever_called)
        self.assertEqual(len(bot.loop.tasks), 5)
        self.assertEqual(
            bot.loop.tasks,
            ['reauthorize-marker', 'refresh-races-marker', 'race-scheduler-marker', 'flagset-registry-marker', 'season-record-marker'],
        )
        # Verify _build_league_scheduler was never called
        bot._build_league_scheduler.assert_not_called()

    def test_run_creates_six_tasks_and_builds_league_when_league_is_enabled(self):
        """When league_enabled=True and scheduler succeeds, run() creates five tasks."""
        bot = bot_for_league(league_enabled=True)
        bot.loop = FakeLoop()
        bot.handle_exception = Mock()
        bot.reauthorize = lambda: 'reauthorize-marker'
        bot.refresh_races = lambda: 'refresh-races-marker'
        bot.race_scheduler = lambda: 'race-scheduler-marker'
        bot._build_league_scheduler = Mock(return_value=Mock())
        # Mock the scheduler.run() coroutine
        scheduler = Mock()
        scheduler.run = lambda: 'league-scheduler-marker'
        bot._build_league_scheduler.return_value = scheduler

        bot.run()

        self.assertTrue(bot.loop.run_forever_called)
        self.assertEqual(len(bot.loop.tasks), 6)
        self.assertEqual(
            bot.loop.tasks,
            ['reauthorize-marker', 'refresh-races-marker', 'race-scheduler-marker', 'flagset-registry-marker', 'season-record-marker', 'league-scheduler-marker'],
        )
        # Verify _build_league_scheduler was called exactly once
        bot._build_league_scheduler.assert_called_once()


if __name__ == '__main__':
    unittest.main()
