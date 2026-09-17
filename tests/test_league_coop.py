import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

from ttpbot.config import TIMEZONE
from ttpbot.league.coop import CoopMatch, group_coop_matches
from ttpbot.league.matchups import Fixture
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace

START = datetime(2026, 9, 20, 20, 0, tzinfo=TIMEZONE)
COOP = Fixture(week=3, away='Bow Mode', home='Shadow Cartel',
               label='Week 3 - Coop Info Share - 2023 Rookie Rumble')
ONE_V_ONE = Fixture(week=2, away='Bow Mode', home='Shadow Cartel', label='Week 2 - TTP3 Power')
#: Inside the warning window, so held matches are reported.
SOON = START - timedelta(minutes=30)


def home(name, rid, did):
    return Racer(sheet_name=name, team='SC', team_full='Shadow Cartel',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id=rid, discord_id=did)


def away(name, rid, did):
    return Racer(sheet_name=name, team='BM', team_full='Bow Mode',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id=rid, discord_id=did)


def rows(fixture=COOP, channel_one='Z1Rracing', channel_two=None,
         start_two=START, game_two=1, tracker_two=None):
    # Sheet order deliberately puts the home runner first in row one.
    one = LeagueRace(start=START, runner_one=home('SirLinkalot', 'rt-sir', '111'),
                     runner_two=away('Windfox470', 'rt-wind', '222'),
                     channel=channel_one, comms=('Bogie',), tracker='droois',
                     game=1, fixture=fixture)
    two = LeagueRace(start=start_two, runner_one=away('seanfreston', 'rt-sean', '333'),
                     runner_two=home('Stags28', 'rt-stags', '444'),
                     channel=channel_two, comms=('Bogie', 'ISUMatt'), tracker=tracker_two,
                     game=game_two, fixture=fixture)
    return [one, two]


class LeagueRaceCoopDefaultsTests(unittest.TestCase):
    def test_a_1v1_race_is_not_coop(self):
        race = rows(fixture=ONE_V_ONE)[0]
        self.assertFalse(race.coop)
        self.assertEqual(race.invite_ids, ('rt-sir', 'rt-wind'))
        self.assertEqual(race.trackers, ('droois',))
        self.assertEqual(rows(fixture=ONE_V_ONE)[1].trackers, ())


class GroupCoopMatchesTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()

    def group(self, races, now=SOON):
        return group_coop_matches(races, self.logger, now=now)

    def test_two_rows_of_one_fixture_become_one_match(self):
        grouped = self.group(rows())
        self.assertEqual(len(grouped), 1)
        self.assertIsInstance(grouped[0], CoopMatch)

    def test_1v1_rows_pass_through_untouched(self):
        original = rows(fixture=ONE_V_ONE)
        self.assertEqual(self.group(original), original)

    def test_keeps_sheet_order_around_a_match(self):
        before = LeagueRace(start=START - timedelta(hours=1),
                            runner_one=home('A', 'rt-a', '1'), runner_two=away('B', 'rt-b', '2'))
        after = LeagueRace(start=START + timedelta(hours=1),
                           runner_one=home('C', 'rt-c', '3'), runner_two=away('D', 'rt-d', '4'))
        grouped = self.group([before] + rows() + [after])
        self.assertEqual([type(item).__name__ for item in grouped],
                         ['LeagueRace', 'CoopMatch', 'LeagueRace'])

    def test_a_lone_coop_row_opens_nothing(self):
        self.assertEqual(self.group(rows()[:1]), [])
        self.logger.warning.assert_called()

    def test_rows_at_different_times_are_not_one_match(self):
        self.assertEqual(self.group(rows(start_two=START + timedelta(minutes=30))), [])

    def test_rows_naming_different_games_are_refused(self):
        self.assertEqual(self.group(rows(game_two=2)), [])
        self.assertIn('games', self.logger.warning.call_args.args[-1])

    def test_the_same_racer_twice_is_refused(self):
        one, _ = rows()
        two = LeagueRace(start=START, runner_one=away('Windfox470', 'rt-wind', '222'),
                         runner_two=home('Stags28', 'rt-stags', '444'), game=1, fixture=COOP)
        self.assertEqual(self.group([one, two]), [])

    def test_a_row_that_does_not_line_up_with_the_fixture_is_refused(self):
        one, _ = rows()
        both_home = LeagueRace(start=START, runner_one=home('seanfreston', 'rt-sean', '333'),
                               runner_two=home('Stags28', 'rt-stags', '444'), game=1, fixture=COOP)
        self.assertEqual(self.group([one, both_home]), [])

    def test_a_name_that_would_break_the_title_is_refused(self):
        one, _ = rows()
        two = LeagueRace(start=START, runner_one=away('sean & co', 'rt-sean', '333'),
                         runner_two=home('Stags28', 'rt-stags', '444'), game=1, fixture=COOP)
        self.assertEqual(self.group([one, two]), [])

    def test_a_far_future_incomplete_match_is_held_quietly(self):
        # Held immediately, but not reported every minute for days: nobody
        # can act on it until the scheduling window.
        self.assertEqual(self.group(rows()[:1], now=START - timedelta(days=2)), [])
        self.logger.warning.assert_not_called()

    def test_a_past_broken_match_is_dropped_quietly(self):
        self.assertEqual(self.group(rows()[:1], now=START + timedelta(hours=3)), [])
        self.logger.warning.assert_not_called()


class CoopMatchTests(unittest.TestCase):
    def setUp(self):
        self.match = group_coop_matches(rows(), Mock())[0]

    def test_orders_runners_away_team_first(self):
        self.assertEqual([r.display_name for r in self.match.away_runners], ['Windfox470', 'seanfreston'])
        self.assertEqual([r.display_name for r in self.match.home_runners], ['SirLinkalot', 'Stags28'])

    def test_invites_all_four_away_then_home(self):
        self.assertEqual(self.match.invite_ids, ('rt-wind', 'rt-sean', 'rt-sir', 'rt-stags'))

    def test_titles_the_room_with_both_teams(self):
        self.assertEqual(self.match.title, 'League: Windfox470 & seanfreston vs. SirLinkalot & Stags28')

    def test_has_a_state_store_compatible_key(self):
        self.assertEqual(self.match.slug, 'coop-bow-mode-vs-shadow-cartel')
        self.assertEqual(self.match.key, START.isoformat() + '|coop-bow-mode-vs-shadow-cartel')

    def test_features_only_rows_with_a_channel(self):
        self.assertEqual([row.channel for row in self.match.featured_rows], ['Z1Rracing'])
        self.assertEqual(self.match.channel, 'Z1Rracing')

    def test_two_rows_on_one_channel_feature_once(self):
        match = group_coop_matches(rows(channel_two='z1rracing'), Mock())[0]
        self.assertEqual(len(match.featured_rows), 1)

    def test_two_channels_feature_both_rows(self):
        match = group_coop_matches(rows(channel_two='Z1Rracing2'), Mock())[0]
        self.assertEqual([row.channel for row in match.featured_rows], ['Z1Rracing', 'Z1Rracing2'])

    def test_crew_is_merged_without_repeats(self):
        self.assertEqual(self.match.comms, ('Bogie', 'ISUMatt'))
        self.assertEqual(self.match.trackers, ('droois',))

    def test_different_trackers_are_both_credited(self):
        match = group_coop_matches(rows(tracker_two='YmustUhateME'), Mock())[0]
        self.assertEqual(match.trackers, ('droois', 'YmustUhateME'))

    def test_no_channel_means_no_channel(self):
        match = group_coop_matches(rows(channel_one=None), Mock())[0]
        self.assertEqual(match.featured_rows, ())
        self.assertIsNone(match.channel)
