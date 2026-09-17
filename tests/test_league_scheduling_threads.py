import asyncio
from datetime import date, datetime
import logging
import unittest

from ttpbot.config import TIMEZONE
from ttpbot.league.matchups import Fixture, parse_matchups
from ttpbot.league.roster import Racer, Roster
from ttpbot.league.scheduling_threads import (
    OPEN_WINDOW,
    thread_key,
    build_thread_post,
    open_at,
    open_week_threads,
    thread_name,
    week_due,
)

STARTS = {3: date(2026, 9, 15), 4: date(2026, 9, 22)}
LOGGER = logging.getLogger('test')


def _racer(name, team_full, team, discord_id):
    return Racer(
        sheet_name=name, team=team, team_full=team_full, display_name=name,
        twitch_channel=name.lower(), racetime_id='rt-' + name.lower(), discord_id=discord_id,
    )


def _roster():
    return Roster([
        _racer('Alice', 'Fahrenheit 451', '451', '1'),
        _racer('Bob', 'Fahrenheit 451', '451', '2'),
        _racer('Cara', 'Fahrenheit 451', '451', None),
        _racer('Dan', 'Shadow Cartel', 'SC', '4'),
        _racer('Eve', 'Shadow Cartel', 'SC', '5'),
        _racer('Finn', 'Shadow Cartel', 'SC', '6'),
    ])


class FakeThreads:
    configured = True

    def __init__(self, fail_on=None):
        self.created = []
        self.posts = []
        self.fail_on = fail_on

    async def create_thread(self, name):
        if self.fail_on and self.fail_on in name:
            raise RuntimeError('discord said no')
        self.created.append(name)
        return 'thread-{}'.format(len(self.created))

    async def post(self, thread_id, post):
        self.posts.append((thread_id, post))


class WeekTimingTests(unittest.TestCase):
    def test_threads_open_at_7pm_the_evening_before_the_week(self):
        self.assertEqual(
            open_at(4, STARTS),
            datetime(2026, 9, 21, 19, 0, tzinfo=TIMEZONE),
        )

    def test_week_is_due_from_its_opening_time_until_the_window_closes(self):
        opens = open_at(4, STARTS)
        self.assertIsNone(week_due(opens.replace(hour=18, minute=59), STARTS))
        self.assertEqual(week_due(opens, STARTS), 4)
        self.assertEqual(week_due(opens + OPEN_WINDOW / 2, STARTS), 4)
        self.assertIsNone(week_due(opens + OPEN_WINDOW, STARTS))

    def test_an_unknown_week_has_no_opening_time(self):
        self.assertIsNone(open_at(9, STARTS))
        self.assertIsNone(week_due(datetime(2026, 12, 1, 19, 0, tzinfo=TIMEZONE), STARTS))


class ThreadPostTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture(week=4, away='Fahrenheit 451', home='Shadow Cartel')
        self.post = build_thread_post(4, self.fixture, _roster(), 'TC #29: oJ5LOsot2')

    def test_thread_name_carries_the_week_and_the_sides(self):
        self.assertEqual(thread_name(4, self.fixture), 'Week 4 - Fahrenheit 451 @ Shadow Cartel')
        long_fixture = Fixture(week=4, away='A' * 80, home='B' * 80)
        self.assertLessEqual(len(thread_name(4, long_fixture)), 100)

    def test_post_states_the_week_sides_and_declaration_order(self):
        content = self.post.content
        self.assertIn('Week 4 - TC #29: oJ5LOsot2', content)
        self.assertIn('**Fahrenheit 451** (away for game 1)', content)
        self.assertIn('**Shadow Cartel** (home for game 1)', content)
        self.assertIn('**Fahrenheit 451** declare their racer(s) first', content)
        self.assertIn('the sides swap', content)

    def test_every_rostered_player_is_pinged_and_only_them(self):
        self.assertEqual(self.post.allowed_user_ids, ['1', '2', '4', '5', '6'])
        for mention in ('<@1>', '<@2>', '<@4>', '<@5>', '<@6>'):
            self.assertIn(mention, self.post.content)
        # No Discord id: named rather than dropped from their own team's line.
        self.assertIn('Cara', self.post.content)

    def test_a_team_the_roster_does_not_know_is_called_out_not_silently_empty(self):
        post = build_thread_post(4, Fixture(week=4, away='Ghost Team', home='Shadow Cartel'), _roster())
        self.assertIn('roster not found', post.content)
        self.assertEqual(post.allowed_user_ids, ['4', '5', '6'])


class OpenWeekThreadsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixtures = [
            Fixture(week=4, away='Fahrenheit 451', home='Shadow Cartel'),
            Fixture(week=4, away='Shadow Cartel', home='Fahrenheit 451'),
        ]

    async def test_opens_one_thread_per_fixture_and_never_twice(self):
        threads = FakeThreads()
        opened = set()
        created = await open_week_threads(
            4, self.fixtures, _roster(), threads, opened, LOGGER, 'TC #29', STARTS)
        self.assertEqual(len(threads.created), 2)
        self.assertEqual(len(threads.posts), 2)
        opened.update(created)

        again = await open_week_threads(
            4, self.fixtures, _roster(), threads, opened, LOGGER, 'TC #29', STARTS)
        self.assertEqual(again, [])
        self.assertEqual(len(threads.created), 2)

    async def test_one_failed_fixture_does_not_cost_the_others(self):
        threads = FakeThreads(fail_on='Fahrenheit 451 @ Shadow Cartel')
        created = await open_week_threads(
            4, self.fixtures, _roster(), threads, set(), LOGGER, None, STARTS)
        self.assertEqual(len(created), 1)
        self.assertEqual(threads.created, ['Week 4 - Shadow Cartel @ Fahrenheit 451'])


class ThreadKeyTests(unittest.TestCase):
    def test_key_is_the_week_opening_timestamp_and_the_fixture(self):
        key = thread_key(4, Fixture(week=4, away='Fahrenheit 451', home='Shadow Cartel'), STARTS)
        timestamp, _, slug = key.partition('|')
        self.assertEqual(timestamp, '2026-09-21T19:00:00-04:00')
        self.assertEqual(slug, 'w4-fahrenheit451-shadowcartel')

    def test_key_survives_the_state_store_rules(self):
        from ttpbot.state import LEAGUE_SLUG
        key = thread_key(4, Fixture(week=4, away='Dodongo Ate My Baby!', home='LKU, etc.'), STARTS)
        self.assertTrue(LEAGUE_SLUG.fullmatch(key.partition('|')[2]))


class MatchupsWeekViewTests(unittest.TestCase):
    CSV = (
        '"Week 4 - TC #29: oJ5LOsot2","",""\n'
        'Division,Away Team,Home Team\n'
        'Hyrule Division,Fahrenheit 451,Shadow Cartel\n'
        'Hyrule Division,TekTitans,Midwest is Best\n'
        '"Week 5 - Coop 4 x 4","",""\n'
        'Division,Away Team,Home Team\n'
        'Lorule Division,Bow Mode,The Last Resorts\n'
    )

    def test_a_week_lists_its_fixtures_in_sheet_order_with_its_label(self):
        matchups = parse_matchups(self.CSV, LOGGER)
        week4 = matchups.fixtures_for_week(4)
        self.assertEqual([(f.away, f.home) for f in week4],
                         [('Fahrenheit 451', 'Shadow Cartel'), ('TekTitans', 'Midwest is Best')])
        self.assertEqual(matchups.label_for_week(4), 'TC #29: oJ5LOsot2')
        self.assertEqual(matchups.label_for_week(5), 'Coop 4 x 4')
        self.assertEqual(matchups.fixtures_for_week(9), [])

    def test_an_ambiguous_pairing_is_dropped_from_the_week_as_well(self):
        csv_text = self.CSV + 'Lorule Division,The Last Resorts,Bow Mode\n'
        matchups = parse_matchups(csv_text, LOGGER)
        self.assertEqual(matchups.fixtures_for_week(5), [])


class RosterByTeamTests(unittest.TestCase):
    def test_team_lookup_ignores_spelling_and_accepts_the_code(self):
        roster = _roster()
        self.assertEqual([r.display_name for r in roster.by_team('fahrenheit451')],
                         ['Alice', 'Bob', 'Cara'])
        self.assertEqual([r.display_name for r in roster.by_team('SC')], ['Dan', 'Eve', 'Finn'])
        self.assertEqual(roster.by_team(''), [])
        self.assertEqual(roster.by_team('Nobody'), [])


if __name__ == '__main__':
    unittest.main()
