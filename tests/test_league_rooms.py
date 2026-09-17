import unittest
from datetime import datetime

from ttpbot.config import POST_SEASON_GOAL_NAME, TIMEZONE
from ttpbot.league.rooms import league_room_form_data
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace


def _racer(name, racetime_id):
    return Racer(
        sheet_name=name, team='SC', team_full='Shadow Cartel',
        display_name=name, twitch_channel=name.lower(),
        racetime_id=racetime_id, discord_id='1',
    )


RACE = LeagueRace(
    start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
    runner_one=_racer('SirLinkalot', 'rt-sir'),
    runner_two=_racer('Windfox470', 'rt-wind'),
    channel=None,
)


class LeagueRoomFormTests(unittest.TestCase):
    def setUp(self):
        self.form = league_room_form_data(RACE)

    def test_uses_the_beat_the_game_goal(self):
        self.assertEqual(self.form['goal'], POST_SEASON_GOAL_NAME)

    def test_titles_the_room_with_both_racers(self):
        self.assertEqual(self.form['info_user'], 'League: SirLinkalot vs. Windfox470')

    def test_leaves_info_bot_clear_so_the_title_renders_once(self):
        # Racetime renders both fields. Writing the title to each showed it
        # twice in the room, and cost the seed info its place: SahasrahBot
        # writes info_bot when it rolls, so leaving it clear means the room
        # reads title + seed instead of title + title.
        self.assertNotIn('info_bot', self.form)

    def test_room_is_open_so_commentators_can_join(self):
        self.assertEqual(self.form['invitational'], 'false')

    def test_room_is_listed(self):
        self.assertEqual(self.form['unlisted'], 'false')

    def test_every_value_is_a_string(self):
        self.assertTrue(all(isinstance(v, str) for v in self.form.values()))


if __name__ == '__main__':
    unittest.main()


from unittest.mock import Mock

from ttpbot.league.coop import group_coop_matches
from ttpbot.league.matchups import Fixture


def _away(name, racetime_id):
    return Racer(sheet_name=name, team='BM', team_full='Bow Mode',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id=racetime_id, discord_id='1')


COOP_FIXTURE = Fixture(week=3, away='Bow Mode', home='Shadow Cartel', label='Week 3 - Coop Info Share')
COOP_MATCH = group_coop_matches([
    LeagueRace(start=RACE.start, runner_one=_racer('SirLinkalot', 'rt-sir'),
               runner_two=_away('Windfox470', 'rt-wind'), game=1, fixture=COOP_FIXTURE),
    LeagueRace(start=RACE.start, runner_one=_away('seanfreston', 'rt-sean'),
               runner_two=_racer('Stags28', 'rt-stags'), game=1, fixture=COOP_FIXTURE),
], Mock())[0]


class CoopRoomFormTests(unittest.TestCase):
    def setUp(self):
        self.form = league_room_form_data(COOP_MATCH)

    def test_is_unranked(self):
        self.assertEqual(self.form['ranked'], 'false')

    def test_keeps_the_beat_the_game_goal_and_no_team_race(self):
        self.assertEqual(self.form['goal'], POST_SEASON_GOAL_NAME)
        self.assertNotIn('team_race', self.form)

    def test_titles_the_room_with_both_teams(self):
        self.assertEqual(self.form['info_user'],
                         'League: Windfox470 & seanfreston vs. SirLinkalot & Stags28')

    def test_a_1v1_room_does_not_send_ranked(self):
        self.assertNotIn('ranked', league_room_form_data(RACE))
