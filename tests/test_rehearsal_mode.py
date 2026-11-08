"""TTPBOT_REHEARSAL: a temporary second TTPBot for the TTP5 staging rehearsal.

It runs beside production with the same racetime credentials, so it must open
and touch only the rooms its own (staging) engine had it open: no nightly
rooms, no League, Autumn or Corto rooms, and no production tournament rooms.
Its rooms are unlisted and unranked, so it joins them by URL rather than from
the category listing.
"""

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ttpbot.autumn.rooms import autumn_room_form_data
from ttpbot.bot import TTPBot
from ttpbot.provider import RacetimeProvider
from ttpbot.state import UNCERTAIN_RACE

LOG = logging.getLogger('rehearsal-test')
ON = {'TTPBOT_REHEARSAL': 'true', 'Z1RR_TTP_ENABLED': 'true'}
OWN = 'z1r/clever-link-1234'


def race(status='open', name=OWN, info_user='TTP5 Rehearsal — Round 1 — a vs b [W1-25] Game 1', goal='Beat the game'):
    return {'name': name, 'status': {'value': status}, 'goal': {'name': goal}, 'info_user': info_user,
            'info_bot': '', 'websocket_bot_url': '/ws/o/bot/' + name.split('/')[1]}


def bot_with_rooms(created):
    bot = TTPBot.__new__(TTPBot)
    bot.provider = RacetimeProvider('https://racetime.gg', 'z1r')
    bot.logger = LOG
    bot.handlers = {}
    bot.state = {}
    bot.ttp_runner = SimpleNamespace(scheduler=SimpleNamespace(created=created))
    return bot


class RoomForm(unittest.TestCase):
    def test_a_rehearsal_room_is_unlisted_and_unranked(self):
        race_ = SimpleNamespace(identity=SimpleNamespace(game=1), match_id='W1-25', runner_one='a', runner_two='b',
                                best_of=3, rounds=6, event='ttp', edition='5')
        with patch.dict('os.environ', ON, clear=True), \
                patch('ttpbot.autumn.rooms.room_title', return_value='TTP5 Rehearsal — Round 1 — a vs b [W1-25] Game 1'):
            form = autumn_room_form_data(race_)
        self.assertEqual(form['unlisted'], 'true')
        self.assertEqual(form['ranked'], 'false')

    def test_outside_a_rehearsal_rooms_stay_listed_and_ranked(self):
        race_ = SimpleNamespace()
        with patch.dict('os.environ', {}, clear=True), \
                patch('ttpbot.autumn.rooms.room_title', return_value='2026 Autumn Tournament — a vs b [W1-1]'):
            form = autumn_room_form_data(race_)
        self.assertEqual(form['unlisted'], 'false')
        self.assertNotIn('ranked', form)


class OnlyItsOwnRooms(unittest.TestCase):
    def test_only_a_room_its_engine_had_it_open_is_handled(self):
        bot = bot_with_rooms({'ttp-5|W1-25|1': 'https://racetime.gg/' + OWN, 'ttp-5|W1-26|1': UNCERTAIN_RACE})
        with patch.dict('os.environ', ON, clear=True):
            self.assertTrue(bot.should_handle(race()))
            for other in (race(name='z1r/someone-else-5678'),
                          race(name='z1r/league-room-1111', info_user='Z1RR League — Week 8'),
                          race(name='z1r/nightly-2222', goal='TTP Season 5', info_user=''),
                          race(name='z1r/prod-tournament-3333', info_user='TTP Season 5 Tournament — Round 1 — a vs b [W1-25] Game 1')):
                with self.subTest(room=other['name']):
                    self.assertFalse(bot.should_handle(other))

    def test_a_finished_own_room_is_left_alone(self):
        bot = bot_with_rooms({'ttp-5|W1-25|1': 'https://racetime.gg/' + OWN})
        with patch.dict('os.environ', ON, clear=True):
            self.assertFalse(bot.should_handle(race(status='finished')))

    def test_with_no_runner_nothing_is_handled(self):
        bot = bot_with_rooms({})
        bot.ttp_runner = None
        with patch.dict('os.environ', ON, clear=True):
            self.assertFalse(bot.should_handle(race()))


class JoinsByUrl(unittest.IsolatedAsyncioTestCase):
    async def test_an_own_room_is_joined_by_url_once(self):
        bot = bot_with_rooms({'ttp-5|W1-25|1': 'https://racetime.gg/' + OWN})
        bot.loop = asyncio.get_running_loop()
        handler = SimpleNamespace(handle=AsyncMock())
        bot.create_handler = MagicMock(return_value=handler)
        bot._read_room = AsyncMock(return_value=race())
        with patch.dict('os.environ', ON, clear=True):
            await bot._join_own_rooms_once()
            await bot._join_own_rooms_once()
        bot._read_room.assert_awaited_once_with(OWN)
        bot.create_handler.assert_called_once()
        self.assertIn(OWN, bot.handlers)

    async def test_a_finished_or_unreadable_room_is_not_joined(self):
        bot = bot_with_rooms({'ttp-5|W1-25|1': 'https://racetime.gg/' + OWN})
        bot.loop = asyncio.get_running_loop()
        bot.create_handler = MagicMock()
        with patch.dict('os.environ', ON, clear=True):
            bot._read_room = AsyncMock(return_value=race(status='finished'))
            await bot._join_own_rooms_once()
            bot._read_room = AsyncMock(return_value=None)
            await bot._join_own_rooms_once()
        bot.create_handler.assert_not_called()
        self.assertEqual(bot.handlers, {})


class Startup(unittest.TestCase):
    def started(self, env):
        bot = TTPBot.__new__(TTPBot)
        bot.logger = LOG
        bot.league_enabled = True
        names = []

        def create_task(coro):
            names.append(getattr(coro, '__qualname__', 'mock'))
            if hasattr(coro, 'close') and asyncio.iscoroutine(coro):
                coro.close()

        bot.loop = SimpleNamespace(create_task=create_task, set_exception_handler=lambda handler: None,
                                   run_forever=lambda: None)
        bot.ttp_runner = None
        bot._build_league_scheduler = MagicMock(return_value=None)
        bot._build_nightly_booths = MagicMock(return_value=None)
        bot._build_autumn_runner = MagicMock(return_value=None)
        bot._build_corto_runner = MagicMock(return_value=None)
        bot._build_ttp_runner = MagicMock(return_value=None)
        with patch.dict('os.environ', env, clear=True), patch('ttpbot.bot.season_record'), \
                patch('ttpbot.bot.flagset_registry'):
            bot.run()
        return bot, names

    def test_a_rehearsal_opens_no_nightly_rooms_and_scans_no_category(self):
        bot, names = self.started(ON)
        self.assertNotIn('TTPBot.race_scheduler', names)
        self.assertNotIn('Bot.refresh_races', names)
        self.assertIn('TTPBot.join_own_rooms', names)
        for builder in ('_build_league_scheduler', '_build_nightly_booths', '_build_autumn_runner', '_build_corto_runner'):
            getattr(bot, builder).assert_not_called()
        bot._build_ttp_runner.assert_called_once()

    def test_production_still_starts_its_scheduler_and_scan(self):
        bot, names = self.started({})
        self.assertIn('TTPBot.race_scheduler', names)
        self.assertIn('Bot.refresh_races', names)
        self.assertNotIn('TTPBot.join_own_rooms', names)


if __name__ == '__main__':
    unittest.main()
