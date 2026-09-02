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
        self.assertEqual(self.form['info_bot'], 'League: SirLinkalot vs. Windfox470')

    def test_room_is_open_so_commentators_can_join(self):
        self.assertEqual(self.form['invitational'], 'false')

    def test_room_is_listed(self):
        self.assertEqual(self.form['unlisted'], 'false')

    def test_every_value_is_a_string(self):
        self.assertTrue(all(isinstance(v, str) for v in self.form.values()))


if __name__ == '__main__':
    unittest.main()
