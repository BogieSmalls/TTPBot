import logging
import unittest
from datetime import datetime
from unittest.mock import Mock

from ttpbot.config import TIMEZONE
from ttpbot.league.roster import Racer, Roster
from ttpbot.league.schedule import LeagueRace, parse_schedule

HEADER = 'Date,Time,Game,Runner 1,Runner 2,,Comms,Tracker,,Channel,Booth\n'

QUIET = logging.getLogger('test-league-schedule')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


def _racer(name, team, racetime_id, discord_id='1'):
    return Racer(
        sheet_name=name, team=team, team_full=team,
        display_name=name, twitch_channel=name.lower(),
        racetime_id=racetime_id, discord_id=discord_id,
    )


ROSTER = Roster([
    _racer('Droois', 'SC', 'rt-droois'),
    _racer('Rhinohero', 'F451', 'rt-rhino'),
    _racer('seanfreston', 'DAMB', 'rt-sean'),
    _racer('Stags28', '3HM', 'rt-stags'),
    _racer('SirLinkalot', '8BW', 'rt-sir'),
    _racer('Windfox470', 'BM', 'rt-wind'),
])


class ParseScheduleTests(unittest.TestCase):
    def parse(self, body):
        return parse_schedule(HEADER + body, ROSTER, QUIET)

    def test_parses_a_row_into_a_league_race(self):
        races = self.parse(
            '9/2/2026,9:00:00 PM,2,(SC) Droois,(F451) Rhinohero,,Sean,,,Z1Rracing,Sean\n'
        )

        self.assertEqual(len(races), 1)
        race = races[0]
        self.assertEqual(race.start, datetime(2026, 9, 2, 21, 0, tzinfo=TIMEZONE))
        self.assertEqual(race.runner_one.sheet_name, 'Droois')
        self.assertEqual(race.runner_two.sheet_name, 'Rhinohero')
        self.assertEqual(race.channel, 'Z1Rracing')

    def test_blank_channel_becomes_none(self):
        races = self.parse('9/3/2026,9:00:00 PM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n')
        self.assertIsNone(races[0].channel)

    def test_two_races_in_one_slot_both_survive(self):
        # League nights run 1v1 and 2v2, so concurrent rooms are normal.
        races = self.parse(
            '9/3/2026,8:00:00 PM,1,(DAMB) seanfreston,(3HM) Stags28,,,,,,\n'
            '9/3/2026,8:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(len(races), 2)
        self.assertEqual(races[0].start, races[1].start)
        self.assertNotEqual(races[0].key, races[1].key)

    def test_unresolvable_runner_skips_only_that_row(self):
        races = self.parse(
            '9/3/2026,8:00:00 PM,1,(XX) NotARacer,(3HM) Stags28,,,,,,\n'
            '9/3/2026,9:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(len(races), 1)
        self.assertEqual(races[0].runner_one.sheet_name, 'SirLinkalot')

    def test_malformed_date_skips_only_that_row(self):
        races = self.parse(
            'not-a-date,8:00:00 PM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n'
            '9/3/2026,9:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )
        self.assertEqual(len(races), 1)

    def test_short_and_blank_rows_are_ignored(self):
        races = self.parse(',,,,,,,,,,\n9/3/2026\n')
        self.assertEqual(races, [])

    def test_populated_short_rows_are_logged_but_blank_rows_stay_silent(self):
        logger = Mock()
        parse_schedule(HEADER + ',,,,,,,,,,\n9/3/2026\n', ROSTER, logger)

        # A wholesale shape change (e.g. an HTML sign-in page instead of
        # the sheet) should be visible; a trailing blank line should not
        # spam a warning every tick.
        logger.warning.assert_called_once()
        message, row_number, column_count, minimum = logger.warning.call_args.args
        self.assertEqual(row_number, 3)
        self.assertEqual(column_count, 1)

    def test_same_racer_twice_is_rejected(self):
        races = self.parse('9/3/2026,8:00:00 PM,1,(SC) Droois,(SC) Droois,,,,,,\n')
        self.assertEqual(races, [])

    def test_handles_daylight_saving_edges(self):
        # 2026-11-01 is the US Eastern fall-back date; 2026-03-08 is spring-forward.
        races = self.parse(
            '11/1/2026,9:00:00 PM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n'
            '3/8/2026,9:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(len(races), 2)
        self.assertEqual(races[0].start.utcoffset().total_seconds(), -5 * 3600)
        self.assertEqual(races[1].start.utcoffset().total_seconds(), -4 * 3600)

    def test_accepts_midnight_and_noon(self):
        races = self.parse(
            '9/3/2026,12:00:00 AM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n'
            '9/3/2026,12:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(races[0].start.hour, 0)
        self.assertEqual(races[1].start.hour, 12)


class LeagueRaceIdentityTests(unittest.TestCase):
    def make(self, one, two):
        return LeagueRace(
            start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
            runner_one=ROSTER.resolve(one),
            runner_two=ROSTER.resolve(two),
            channel=None,
        )

    def test_title_uses_sheet_column_order(self):
        race = self.make('Windfox470', 'SirLinkalot')
        self.assertEqual(race.title, 'League: Windfox470 vs. SirLinkalot')

    def test_key_is_independent_of_runner_column_order(self):
        # Swapping the two runner columns must not open a second room.
        self.assertEqual(
            self.make('SirLinkalot', 'Windfox470').key,
            self.make('Windfox470', 'SirLinkalot').key,
        )

    def test_key_starts_with_an_iso_timestamp_then_a_pipe(self):
        key = self.make('SirLinkalot', 'Windfox470').key
        stamp, _, slug = key.partition('|')
        self.assertEqual(datetime.fromisoformat(stamp).hour, 20)
        self.assertEqual(slug, 'sirlinkalot-vs-windfox470')

    def test_slug_is_lowercase_alphanumeric_and_dashes(self):
        slug = self.make('SirLinkalot', 'Windfox470').slug
        self.assertRegex(slug, r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


if __name__ == '__main__':
    unittest.main()
