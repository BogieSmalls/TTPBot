"""Starting the Autumn runner, and not starting it.

Off is the default and off is safe. A relay missing the engine token, the flag or
the racetime credentials must run the League exactly as it did before -- which is
why the builder has its own try and its own log line, and why every collaborator
is optional rather than required.
"""

import asyncio
import logging
import unittest

from ttpbot.autumn.wiring import (
    DEFAULT_SCHEDULE_URL,
    AutumnRunner,
    build_autumn_runner,
    _round_label,
)


class Log(logging.Logger):
    def __init__(self):
        super().__init__('autumn')
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, msg, *args, **kw):
        self.infos.append(msg % args if args else msg)

    def warning(self, msg, *args, **kw):
        self.warnings.append(msg % args if args else msg)

    def error(self, msg, *args, **kw):
        self.errors.append(msg % args if args else msg)


class Bot:
    provider = None
    access_token = 'token'
    autumn_webhook_url = 'https://discord/webhook'


def quiet_engine(runner, racetime_ids=None):
    """Stop the id cache reaching for a socket when it misses."""
    async def state():
        return {'document': {'racetimeIds': dict(racetime_ids or {})}}

    runner.engine.state = state
    return runner


class Building(unittest.TestCase):
    def setUp(self):
        self.log = Log()

    def test_no_engine_token_means_off(self):
        runner = build_autumn_runner({}, Bot(), self.log)
        self.assertFalse(runner.configured)
        self.assertTrue(any('stays off' in i for i in self.log.infos))

    def test_the_token_is_enough_to_be_configured(self):
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, Bot(), self.log)
        self.assertTrue(runner.configured)

    def test_it_reads_the_schedule_tab_by_default(self):
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, Bot(), self.log)
        self.assertEqual(runner.scheduler.source.url, DEFAULT_SCHEDULE_URL)
        # The Schedule tab of the League master sheet, as CSV.
        self.assertIn('gid=2033319762', runner.scheduler.source.url)
        self.assertIn('format=csv', runner.scheduler.source.url)

    def test_the_schedule_url_can_be_overridden(self):
        # So a rehearsal can point at a copy without touching the real tab.
        runner = build_autumn_runner(
            {'Z1RR_ENGINE_TOKEN': 'x', 'Z1RR_AUTUMN_SCHEDULE_URL': 'https://copy'},
            Bot(), self.log)
        self.assertEqual(runner.scheduler.source.url, 'https://copy')

    def test_the_engine_url_defaults_to_the_relay_port(self):
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, Bot(), self.log)
        self.assertEqual(runner.engine._url, 'http://127.0.0.1:3007')

    def test_no_relay_means_no_booth_is_woken(self):
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, Bot(), self.log)
        self.assertIsNone(runner.scheduler._wake_booth)
        self.assertTrue(any('no relay wake' in i for i in self.log.infos))

    def test_the_wake_uses_the_leagues_own_token(self):
        # The same env var, so an operator does not have to issue a second token
        # for the same endpoint.
        runner = build_autumn_runner(
            {'Z1RR_ENGINE_TOKEN': 'x', 'Z1RR_RELAY_URL': 'http://127.0.0.1:3005',
             'Z1RR_WAKE_TOKEN': 'wake'},
            Bot(), self.log)
        self.assertIsNotNone(runner.scheduler._wake_booth)

    def test_the_invite_list_is_seeded_where_the_handler_reads_it(self):
        # The handler that holds the room's websocket sends the invites; this
        # writes the list into `bot.state[race_name]`, which `create_handler`
        # passes to it by reference. The same mechanism the League uses.
        from ttpbot.autumn.matching import RaceIdentity, Resolved
        from datetime import datetime

        bot = Bot()
        bot.state = {}
        bot.autumn_racetime_ids = {'ISUMatt': 'aaa', 'chessjerk': 'bbb'}
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, bot, self.log)

        race = Resolved(
            identity=RaceIdentity(event='autumn', match_id='W1-1'),
            match_id='W1-1', at=datetime(2026, 10, 2, 22, 0),
            runner_one='ISUMatt', runner_two='chessjerk')
        asyncio.run(runner.scheduler._invite(
            race, 'https://racetime.gg/z1r/fancy-mario-1234'))

        seeded = bot.state['z1r/fancy-mario-1234']['autumn_race']
        self.assertEqual(seeded['invite'], ['aaa', 'bbb'])
        self.assertIn('[W1-1]', seeded['title'])

    def test_a_racer_with_no_racetime_id_means_nobody_is_invited(self):
        # Both or neither. Inviting one leaves the other looking at a room they
        # are not in, which is worse than inviting nobody and saying so -- the
        # room is public, so they can join it themselves.
        from ttpbot.autumn.matching import RaceIdentity, Resolved
        from datetime import datetime

        bot = Bot()
        bot.state = {}
        bot.autumn_racetime_ids = {'ISUMatt': 'aaa'}
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, bot, self.log)
        # A miss re-reads the engine before giving up, so the engine answers here
        # rather than the test waiting on a socket that is not listening.
        quiet_engine(runner, {'ISUMatt': 'aaa'})

        race = Resolved(
            identity=RaceIdentity(event='autumn', match_id='W1-1'),
            match_id='W1-1', at=datetime(2026, 10, 2, 22, 0),
            runner_one='ISUMatt', runner_two='chessjerk')
        asyncio.run(runner.scheduler._invite(
            race, 'https://racetime.gg/z1r/fancy-mario-1234'))

        self.assertEqual(bot.state['z1r/fancy-mario-1234']['autumn_race']['invite'], [])
        self.assertTrue(
            any('chessjerk' in w for w in self.log.warnings), self.log.warnings)

    def test_seeding_does_not_clobber_what_else_is_in_the_room_state(self):
        from ttpbot.autumn.matching import RaceIdentity, Resolved
        from datetime import datetime

        bot = Bot()
        bot.state = {'z1r/fancy-mario-1234': {'something_else': True}}
        bot.autumn_racetime_ids = {'ISUMatt': 'aaa', 'chessjerk': 'bbb'}
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, bot, self.log)
        race = Resolved(
            identity=RaceIdentity(event='autumn', match_id='W1-1'),
            match_id='W1-1', at=datetime(2026, 10, 2, 22, 0),
            runner_one='ISUMatt', runner_two='chessjerk')
        asyncio.run(runner.scheduler._invite(
            race, 'https://racetime.gg/z1r/fancy-mario-1234'))
        self.assertTrue(bot.state['z1r/fancy-mario-1234']['something_else'])


class RoundLabels(unittest.TestCase):
    def test_the_two_finals_are_named(self):
        self.assertEqual(_round_label('GF-1'), 'Grand Final')
        self.assertEqual(_round_label('GF-2'), 'Grand Final Reset')

    def test_everything_else_falls_back_to_the_match_id(self):
        # A label guessed from a bracket this does not model would put
        # "Semifinal" on the wrong room, and the match id is never wrong.
        self.assertIsNone(_round_label('W1-1'))
        self.assertIsNone(_round_label('L6-2'))


class Startup(unittest.TestCase):
    """The bot only starts it when asked, and never at the League's expense."""

    def runner_for(self, env):
        from unittest import mock

        from ttpbot.bot import TTPBot

        bot = mock.Mock(spec=TTPBot)
        bot.logger = Log()
        bot._build_autumn_runner = TTPBot._build_autumn_runner.__get__(bot, TTPBot)
        with mock.patch.dict('os.environ', env, clear=True):
            return bot._build_autumn_runner(), bot.logger

    def test_off_without_the_flag(self):
        runner, _ = self.runner_for({})
        self.assertIsNone(runner)

    def test_off_with_the_flag_but_no_token(self):
        runner, _ = self.runner_for({'Z1RR_AUTUMN_ENABLED': 'true'})
        self.assertIsNone(runner)

    def test_an_unusable_setup_is_logged_and_not_fatal(self):
        # `data_dir` is a Mock, so building the stores raises. The League and TTP
        # must be unaffected, which means a None and a log line rather than a
        # traceback out of `run`.
        runner, log = self.runner_for({
            'Z1RR_AUTUMN_ENABLED': 'true', 'Z1RR_ENGINE_TOKEN': 'x'})
        self.assertIsNone(runner)
        self.assertTrue(
            any('unaffected' in e for e in log.errors), log.errors)


if __name__ == '__main__':
    unittest.main()
