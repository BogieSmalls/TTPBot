import logging
import unittest
from datetime import datetime
from unittest.mock import Mock

from ttpbot.config import TIMEZONE
from ttpbot.league.broadcast_request import build_broadcast_request
from ttpbot.league.matchups import Fixture
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace

QUIET = logging.getLogger('test-league-broadcast-request')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


def racer(name, team, team_full, twitch):
    return Racer(
        sheet_name=name, team=team, team_full=team_full, display_name=name,
        twitch_channel=twitch, racetime_id='rt-' + name.lower(), discord_id='1',
    )


CUSTOM = racer('cUstOm', 'MiB', 'Midwest Is Best', 'customshield')
OZZ = racer('OzzSolo', 'TUG', 'Three Unique Gamers', 'ozzsolo')


class _Crew:
    def __init__(self, mapping):
        self._mapping = mapping

    def user_id_for(self, name):
        return self._mapping.get(name)


CREW = _Crew({'Bogie': 'u-a', 'YmustUhateME': 'u-b', 'droois': 'u-c'})


def race(**overrides):
    defaults = dict(
        start=datetime(2026, 9, 6, 23, 0, tzinfo=TIMEZONE),
        runner_one=CUSTOM,
        runner_two=OZZ,
        channel='Z1Rracing',
        comms=('Bogie', 'YmustUhateME'),
        tracker='droois',
        game=2,
        # TUG away at MiB - the reverse of the Schedule row's order.
        fixture=Fixture(week=1, away='Three Unique Gamers', home='Midwest is Best'),
    )
    defaults.update(overrides)
    return LeagueRace(**defaults)


class BuildBroadcastRequestTests(unittest.TestCase):
    def setUp(self):
        self.payload = build_broadcast_request(
            race(), 'z1r/adequate-link-4500', CREW, QUIET,
        )

    def test_puts_the_away_team_in_slot_one(self):
        # The Schedule row reads cUstOm then OzzSolo; the fixture says TUG are
        # away. Taking slot order from the Schedule would put the teams on the
        # wrong sides about half the time.
        self.assertEqual(self.payload['racers'], [
            {'slot': 1, 'channel': 'ozzsolo', 'displayName': 'OzzSolo'},
            {'slot': 2, 'channel': 'customshield', 'displayName': 'cUstOm'},
        ])

    def test_titles_with_the_week_abbreviations_and_game(self):
        self.assertEqual(self.payload['title'], 'Z1RR League Week 1\nTUG vs. MiB Game 2')

    def test_carries_the_room_and_the_channel(self):
        self.assertEqual(self.payload['raceSlug'], 'z1r/adequate-link-4500')
        self.assertEqual(self.payload['twitchChannel'], 'Z1Rracing')

    def test_uses_the_race_key_so_the_endpoint_can_deduplicate(self):
        self.assertEqual(self.payload['leagueKey'], race().key)

    def test_sends_an_iso_timestamp(self):
        self.assertTrue(self.payload['scheduledAt'].startswith('2026-09-07T03:00:00'))

    def test_resolves_crew_to_managed_user_ids(self):
        # Not Discord ids: a draft stores managed-user ids, and passing the
        # wrong one silently invites nobody.
        self.assertEqual(self.payload['commentatorUserIds'], ['u-a', 'u-b'])
        self.assertEqual(self.payload['trackerUserId'], 'u-c')


class UnresolvableTests(unittest.TestCase):
    def test_refuses_a_race_with_no_fixture(self):
        logger = Mock()

        payload = build_broadcast_request(race(fixture=None), 'z1r/x', CREW, logger)

        # No week number and no way to tell which team is away. Guessing is
        # the one thing this must never do.
        self.assertIsNone(payload)
        self.assertTrue(logger.warning.called)

    def test_refuses_a_race_with_no_channel(self):
        payload = build_broadcast_request(race(channel=None), 'z1r/x', CREW, QUIET)

        self.assertIsNone(payload)

    def test_refuses_without_a_race_room(self):
        payload = build_broadcast_request(race(), '', CREW, QUIET)

        # The draft carries raceSlug; a booth with no room to sync from is
        # not worth building.
        self.assertIsNone(payload)

    def test_still_builds_when_a_crew_name_does_not_resolve(self):
        logger = Mock()

        payload = build_broadcast_request(
            race(comms=('Bogie', 'Nobody'), tracker='AlsoNobody'), 'z1r/x', CREW, logger,
        )

        # An uninvited commentator is a person to chase; a missing booth is a
        # broadcast nobody can run. The booth wins.
        self.assertEqual(payload['commentatorUserIds'], ['u-a'])
        self.assertIsNone(payload['trackerUserId'])
        self.assertTrue(logger.warning.called)

    def test_omits_the_game_when_the_sheet_did_not_say(self):
        payload = build_broadcast_request(race(game=None), 'z1r/x', CREW, QUIET)

        self.assertEqual(payload['title'], 'Z1RR League Week 1\nTUG vs. MiB')


if __name__ == '__main__':
    unittest.main()


class DriftBetweenTabsTests(unittest.TestCase):
    """The fixture is looked up by these two teams, so a mismatch means the
    sheets disagreed with each other or with roster.json between fetches."""

    def test_refuses_when_neither_racer_plays_for_the_away_team(self):
        logger = Mock()
        wrong = race(fixture=Fixture(week=1, away='Bow Mode', home='3 Horsemen'))

        self.assertIsNone(wrong.away_racer)
        self.assertFalse(wrong.orchestratable)
        # Returning runner_two here would be a coin flip dressed as an answer.
        self.assertIsNone(build_broadcast_request(wrong, 'z1r/x', CREW, logger))
        self.assertTrue(logger.warning.called)

    def test_refuses_when_both_racers_play_for_the_away_team(self):
        same = racer('Twin', 'MiB', 'Midwest Is Best', 'twin')
        wrong = race(
            runner_one=racer('cUstOm', 'MiB', 'Midwest Is Best', 'customshield'),
            runner_two=same,
            fixture=Fixture(week=1, away='Midwest Is Best', home='Three Unique Gamers'),
        )

        # Not a fixture that can exist, and picking the first is still a guess.
        self.assertIsNone(wrong.away_racer)
        self.assertFalse(wrong.orchestratable)
