import unittest
from datetime import datetime

from ttpbot.config import TIMEZONE
from ttpbot.league.announce import build_announcement
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace

ROOM = 'https://racetime.gg/z1r/clever-slug-1234'


def _racer(name, discord_id):
    return Racer(
        sheet_name=name, team='SC', team_full='Shadow Cartel',
        display_name=name, twitch_channel=name.lower(),
        racetime_id='rt-' + name.lower(), discord_id=discord_id,
    )


def _race(one, two):
    return LeagueRace(
        start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
        runner_one=one, runner_two=two, channel=None,
    )


class BuildAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.race = _race(_racer('SirLinkalot', '111'), _racer('Windfox470', '222'))
        self.body = build_announcement(self.race, ROOM)

    def test_mentions_both_racers(self):
        self.assertIn('<@111>', self.body['content'])
        self.assertIn('<@222>', self.body['content'])

    def test_includes_the_room_url(self):
        self.assertIn(ROOM, self.body['content'])

    def test_allow_lists_exactly_the_two_racers(self):
        self.assertEqual(self.body['allowed_mentions'],
                         {'parse': [], 'users': ['111', '222']})

    def test_parse_is_always_empty(self):
        # A stray @everyone in a spreadsheet cell must not ping the server.
        race = _race(_racer('@everyone', '111'), _racer('Windfox470', '222'))
        body = build_announcement(race, ROOM)
        self.assertEqual(body['allowed_mentions']['parse'], [])

    def test_missing_discord_id_falls_back_to_the_display_name(self):
        race = _race(_racer('SirLinkalot', None), _racer('Windfox470', '222'))
        body = build_announcement(race, ROOM)

        self.assertIn('SirLinkalot', body['content'])
        self.assertNotIn('<@None>', body['content'])
        self.assertNotIn('<@>', body['content'])
        self.assertEqual(body['allowed_mentions']['users'], ['222'])

    def test_no_discord_ids_at_all_still_produces_a_post(self):
        race = _race(_racer('SirLinkalot', None), _racer('Windfox470', None))
        body = build_announcement(race, ROOM)

        self.assertEqual(body['allowed_mentions']['users'], [])
        self.assertIn(ROOM, body['content'])


if __name__ == '__main__':
    unittest.main()
