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


class _StubCrew:
    def __init__(self, mapping):
        self._mapping = mapping

    def mentions(self, names):
        rendered, ids = [], []
        for name in names:
            found = self._mapping.get(name)
            if found:
                rendered.append('<@{}>'.format(found))
                ids.append(found)
            elif name:
                rendered.append(name)
        return rendered, ids


CREW = _StubCrew({'SpecialK': '429', 'GrandpaSzabo': '355'})


def _staffed_race(comms=(), tracker=None):
    return LeagueRace(
        start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
        runner_one=_racer('SirLinkalot', '111'),
        runner_two=_racer('Windfox470', '222'),
        channel='Z1Rracing', comms=comms, tracker=tracker,
    )


class CrewTaggingTests(unittest.TestCase):
    def test_tags_comms_and_tracker_alongside_the_racers(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM, crew=CREW,
        )

        self.assertIn('<@429>', body['content'])
        self.assertIn('<@355>', body['content'])
        # Allow-listed, never parsed: the content is built from a live
        # spreadsheet, so a stray @everyone in a cell must not ping the server.
        self.assertEqual(body['allowed_mentions']['parse'], [])
        self.assertEqual(
            sorted(body['allowed_mentions']['users']), ['111', '222', '355', '429'],
        )

    def test_credits_unresolvable_crew_without_pinging_them(self):
        body = build_announcement(
            _staffed_race(comms=('Nobody',), tracker=None), ROOM, crew=CREW,
        )

        self.assertIn('Nobody', body['content'])
        self.assertNotIn('<@>', body['content'])
        self.assertEqual(sorted(body['allowed_mentions']['users']), ['111', '222'])

    def test_says_nothing_extra_when_no_crew_is_scheduled(self):
        body = build_announcement(_staffed_race(), ROOM, crew=CREW)

        self.assertNotIn('Comms', body['content'])
        self.assertNotIn('Tracker', body['content'])

    def test_works_with_no_crew_directory_at_all(self):
        # The control plane may never have been reachable. The announcement
        # still has to go out.
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM,
        )

        self.assertIn('SpecialK', body['content'])
        self.assertEqual(sorted(body['allowed_mentions']['users']), ['111', '222'])

    def test_separates_the_crew_credits_with_a_blank_line(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM, crew=CREW,
        )

        # Matchup and crew are two separate thoughts; a single break renders
        # too tightly in Discord to scan at a glance.
        self.assertIn('\n\nComms:', body['content'])


class ContinuationAnnouncementTests(unittest.TestCase):
    def test_warns_that_the_channel_is_already_on_air(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM,
            crew=CREW, continuation=True,
        )

        # The booth was not created for this race - the previous one is still
        # on air and the operator swaps the room and racers over. Crew opening
        # the booth mid-show should expect that rather than think it is broken.
        self.assertIn('already ON THE AIR', body['content'])

    def test_says_nothing_extra_for_an_ordinary_race(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM, crew=CREW,
        )

        self.assertNotIn('ON THE AIR', body['content'])

    def test_still_tags_the_crew_on_a_continuation(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM,
            crew=CREW, continuation=True,
        )

        self.assertIn('<@429>', body['content'])
        self.assertEqual(sorted(body['allowed_mentions']['users']), ['111', '222', '355', '429'])


from unittest.mock import Mock

from ttpbot.league.coop import group_coop_matches
from ttpbot.league.matchups import Fixture

COOP_FIXTURE = Fixture(week=3, away='Bow Mode', home='Shadow Cartel', label='Week 3 - Coop Info Share')


def _away(name, discord_id):
    return Racer(sheet_name=name, team='BM', team_full='Bow Mode',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id='rt-' + name.lower(), discord_id=discord_id)


def _coop_match(tracker_one=None, tracker_two=None):
    start = datetime(2026, 9, 20, 20, 0, tzinfo=TIMEZONE)
    return group_coop_matches([
        LeagueRace(start=start, runner_one=_racer('SirLinkalot', '111'),
                   runner_two=_away('Windfox470', '222'), tracker=tracker_one,
                   game=1, fixture=COOP_FIXTURE),
        LeagueRace(start=start, runner_one=_away('seanfreston', '333'),
                   runner_two=_racer('Stags28', '444'), tracker=tracker_two,
                   game=1, fixture=COOP_FIXTURE),
    ], Mock())[0]


class CoopAnnouncementTests(unittest.TestCase):
    def test_names_both_teams_away_first(self):
        body = build_announcement(_coop_match(), ROOM)
        self.assertEqual(body['content'], 'League co-op: <@222> & <@333> vs <@111> & <@444> — ' + ROOM)

    def test_allow_lists_all_four_runners(self):
        body = build_announcement(_coop_match(), ROOM)
        self.assertEqual(body['allowed_mentions'], {'parse': [], 'users': ['111', '222', '333', '444']})

    def test_credits_both_trackers_when_the_rows_differ(self):
        body = build_announcement(_coop_match('droois', 'ISUMatt'), ROOM)
        self.assertIn('Tracker: droois ISUMatt', body['content'])

    def test_the_1v1_line_is_unchanged(self):
        race = _race(_racer('SirLinkalot', '111'), _racer('Windfox470', '222'))
        self.assertEqual(build_announcement(race, ROOM)['content'], 'League: <@111> vs <@222> — ' + ROOM)
