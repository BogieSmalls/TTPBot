"""Championship Week series games (Bogie, 2026-10-24).

Each Best of 5 game is two Schedule rows at one time, like co-op, but all four
racers race together on one broadcast: one unranked room, one four-racer booth.
The series come from the final standings (scheduling_threads.SERIES), so a
pairing that also met in the regular season is not mistaken for that week.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

from ttpbot.config import TIMEZONE
from ttpbot.league.announce import build_announcement
from ttpbot.league.broadcast_request import build_broadcast_request
from ttpbot.league.coop import CoopMatch, group_coop_matches
from ttpbot.league.matchups import Fixture
from ttpbot.league.roster import Racer
from ttpbot.league.rooms import league_room_form_data
from ttpbot.league.schedule import LeagueRace
from ttpbot.league.series import series_fixture_for

START = datetime(2026, 10, 27, 20, 0, tzinfo=TIMEZONE)


def lku(name, rid):
    return Racer(sheet_name=name, team='LKU', team_full='The Missing Links', display_name=name,
                 twitch_channel=name.lower(), racetime_id=rid, discord_id='d-' + rid)


def tek(name, rid):
    return Racer(sheet_name=name, team='Tek', team_full='TekTITANS', display_name=name,
                 twitch_channel=name.lower(), racetime_id=rid, discord_id='d-' + rid)


def series_rows(game=3, channel='Z1Rracing'):
    fixture = series_fixture_for('The Missing Links', 'TekTITANS', START)
    one = LeagueRace(start=START, runner_one=lku('Merks', 'rt-merks'), runner_two=tek('Bogie', 'rt-bogie'),
                     channel=channel, comms=('Shatty',), tracker='droois', game=game, fixture=fixture)
    two = LeagueRace(start=START, runner_one=tek('ThePirateSheep', 'rt-sheep'), runner_two=lku('Thomjay', 'rt-thom'),
                     channel=None, game=game, fixture=fixture)
    return [one, two]


class SeriesFixtureTests(unittest.TestCase):
    def test_a_finalist_pairing_in_championship_week_is_its_series(self):
        fixture = series_fixture_for('TekTITANS', 'The Missing Links', START)
        self.assertEqual((fixture.week, fixture.away, fixture.home, fixture.label, fixture.series),
                         (8, 'TekTITANS', 'The Missing Links', 'Championship Series', True))
        self.assertEqual(series_fixture_for('Bow Mode', 'The Last Resorts', START).label, 'Division Series')

    def test_the_same_teams_in_the_regular_season_are_not(self):
        self.assertIsNone(series_fixture_for('TekTITANS', 'The Missing Links', datetime(2026, 10, 1, 20, 0, tzinfo=TIMEZONE)))
        self.assertIsNone(series_fixture_for('TekTITANS', 'Bow Mode', START), 'not a series pairing')


class SeriesMatchTests(unittest.TestCase):
    def test_the_two_rows_of_a_game_are_one_match_of_four(self):
        [match] = group_coop_matches(series_rows(), Mock(), now=START - timedelta(minutes=30))
        self.assertIsInstance(match, CoopMatch)
        self.assertTrue(match.series)
        self.assertEqual(len(match.invite_ids), 4)
        self.assertEqual(match.slug, 'series-week-8-game-3-tektitans-vs-the-missing-links')

    def test_the_room_is_unranked(self):
        [match] = group_coop_matches(series_rows(), Mock(), now=START)
        self.assertEqual(league_room_form_data(match)['ranked'], 'false')

    def test_the_announcement_names_the_series_not_co_op(self):
        [match] = group_coop_matches(series_rows(), Mock(), now=START)
        embed = build_announcement(match, 'https://racetime.gg/z1r/example-room-1234')['embeds'][0]
        self.assertEqual(embed['title'], 'League Season 1 · Championship Series · Game 3')
        self.assertNotIn('Co-op', embed['title'])
        self.assertNotIn('Championship Series\n', embed['description'], 'no week flagset line: teams pick per game')

    def test_one_booth_shows_all_four_racers(self):
        from dataclasses import replace
        rows = series_rows()
        rows[0] = replace(rows[0], tracker2='Trainee')
        [match] = group_coop_matches(rows, Mock(), now=START)
        crew = Mock(user_id_for=lambda name: 'u-' + name)
        payload = build_broadcast_request(match, 'example-room-1234', crew, Mock(), series=True)
        self.assertEqual([(r['slot'], r['displayName']) for r in payload['racers']],
                         [(1, 'Bogie'), (2, 'Merks'), (3, 'ThePirateSheep'), (4, 'Thomjay')],
                         'lower seed in 1 and 3, higher seed in 2 and 4: each row a pairing')
        self.assertTrue(payload['series'])
        self.assertNotIn('coop', payload)
        # Each racer carries their team, so the booth colors the two columns by team.
        teams = [r['team'] for r in payload['racers']]
        self.assertEqual(teams[0], teams[2])
        self.assertEqual(teams[1], teams[3])
        self.assertNotEqual(teams[0], teams[1])
        self.assertEqual(payload['title'], 'Z1RR League Championship Series\nTek vs. LKU Game 3')
        self.assertEqual(payload['twitchChannel'], 'Z1Rracing')
        self.assertEqual(payload['commentatorUserIds'], ['u-Shatty'])
        self.assertEqual(payload['trackerUserId'], 'u-droois')
        self.assertEqual(payload['tracker2UserId'], 'u-Trainee')
