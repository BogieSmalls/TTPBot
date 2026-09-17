import logging
import unittest
from unittest.mock import Mock

from ttpbot.league.matchups import parse_matchups

QUIET = logging.getLogger('test-league-matchups')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False

# Shaped exactly like the live Matchups tab: a week header carrying the week
# number, the preset name and that week's flag string, then a column header,
# then that week's fixtures with the away team first.
SHEET = '\n'.join([
    '"Week 1 - TTP3 Wisdom: oIbnPfPb0mR7ggY12zwI0QNIY620UnhU8kiC3","",""',
    '"Division","Away Team","Home Team"',
    '"Hyrule Division","Fahrenheit 451","Shadow Cartel"',
    '"Crossover (Rank 1)","Three Unique Gamers","Midwest is Best"',
    '"Week 2 - TTP3 Power: 143oNtDD4PAvBt5G8xyCFu5kwp7tS8vUBVpiZY","",""',
    '"Division","Away Team","Home Team"',
    '"Hyrule Division","Shadow Cartel","Midwest is Best"',
    '"Lorule Division","Lion\'s opening, King\'s gambit, Ultra variation","The missing links"',
    '"Week 8 - Playoffs: xyz","",""',
    '"Division","Away Team","Home Team"',
    '"Semifinal","TBD","TBD"',
    '"Semifinal","TBD","TBD"',
    '',
])


class ParseMatchupsTests(unittest.TestCase):
    def setUp(self):
        self.matchups = parse_matchups(SHEET, QUIET)

    def test_finds_a_fixture_from_either_side(self):
        forward = self.matchups.fixture_for('Fahrenheit 451', 'Shadow Cartel')
        backward = self.matchups.fixture_for('Shadow Cartel', 'Fahrenheit 451')

        # The Schedule tab's Runner 1 / Runner 2 order carries no meaning, so
        # the lookup must not depend on which way round it is asked.
        self.assertEqual(forward, backward)
        self.assertEqual(forward.week, 1)
        self.assertEqual(forward.away, 'Fahrenheit 451')
        self.assertEqual(forward.home, 'Shadow Cartel')

    def test_keeps_each_week_separate(self):
        self.assertEqual(self.matchups.fixture_for('Three Unique Gamers', 'Midwest is Best').week, 1)
        self.assertEqual(self.matchups.fixture_for('Shadow Cartel', 'Midwest is Best').week, 2)

    def test_matches_across_the_spelling_the_two_tabs_disagree_on(self):
        # roster.json says "Midwest Is Best" and "The Missing Links"; the
        # Matchups tab says "Midwest is Best" and "The missing links", and LKU
        # differs by commas as well.
        self.assertIsNotNone(self.matchups.fixture_for('Three Unique Gamers', 'Midwest Is Best'))
        fixture = self.matchups.fixture_for(
            "Lion's Opening King's Gambit Ultra Variation", 'The Missing Links',
        )
        self.assertIsNotNone(fixture)
        self.assertEqual(fixture.week, 2)

    def test_reports_the_orientation_the_booth_needs(self):
        fixture = self.matchups.fixture_for('Midwest Is Best', 'Three Unique Gamers')

        # Away goes in slot 1, home in slot 2 - and the sheet says TUG is away
        # at MiB, which is the reverse of the Schedule row for that race.
        self.assertEqual(fixture.away, 'Three Unique Gamers')
        self.assertEqual(fixture.home, 'Midwest is Best')

    def test_ignores_TBD_placeholder_rows(self):
        # The only pairing that repeats in the whole season is TBD vs TBD,
        # which would otherwise look like an ambiguous fixture.
        self.assertIsNone(self.matchups.fixture_for('TBD', 'TBD'))

    def test_ignores_the_column_header_row(self):
        self.assertIsNone(self.matchups.fixture_for('Away Team', 'Home Team'))

    def test_returns_none_for_a_pairing_that_is_not_scheduled(self):
        self.assertIsNone(self.matchups.fixture_for('Bow Mode', '3 Horsemen'))

    def test_returns_none_rather_than_guessing_when_a_pairing_repeats(self):
        logger = Mock()
        doubled = SHEET + '\n'.join([
            '"Week 9 - Rematch: abc","",""',
            '"Division","Away Team","Home Team"',
            '"Hyrule Division","Shadow Cartel","Fahrenheit 451"',
            '',
        ])

        matchups = parse_matchups(doubled, logger)

        # Home and away reversed is still the same pair of teams. Picking one
        # would put the teams on the wrong sides half the time, so an
        # ambiguous pairing resolves to nothing and says so.
        self.assertIsNone(matchups.fixture_for('Fahrenheit 451', 'Shadow Cartel'))
        self.assertTrue(logger.warning.called)

    def test_survives_a_sheet_it_cannot_read(self):
        logger = Mock()

        matchups = parse_matchups('<!doctype html><html>sign in</html>', logger)

        # Google answers an unauthenticated export with an HTML sign-in page.
        self.assertIsNone(matchups.fixture_for('Fahrenheit 451', 'Shadow Cartel'))
        self.assertEqual(matchups.count, 0)

    def test_counts_the_fixtures_it_understood(self):
        self.assertEqual(self.matchups.count, 4)


if __name__ == '__main__':
    unittest.main()


from ttpbot.league.matchups import Fixture

COOP_SHEET = '\n'.join([
    '"Week 2 - TTP3 Power: 143oNtDD4PAvBt5G8xyCFu5kwp7tS8vUBVpiZY","",""',
    '"Division","Away Team","Home Team"',
    '"Hyrule Division","Shadow Cartel","Midwest is Best"',
    '"Week 3 - Coop Info Share - 2023 Rookie Rumble: abcdef","",""',
    '"Division","Away Team","Home Team"',
    '"Hyrule Division","Fahrenheit 451","Shadow Cartel"',
    '"Week 5 - Co-Op 4x4 - TC #30: xyz","",""',
    '"Division","Away Team","Home Team"',
    '"Hyrule Division","Three Unique Gamers","Midwest is Best"',
    '',
])


class CoopWeekTests(unittest.TestCase):
    def setUp(self):
        self.matchups = parse_matchups(COOP_SHEET, QUIET)

    def test_a_coop_week_header_marks_its_fixtures_coop(self):
        fixture = self.matchups.fixture_for('Fahrenheit 451', 'Shadow Cartel')
        self.assertTrue(fixture.coop)
        self.assertEqual(fixture.label, 'Week 3 - Coop Info Share - 2023 Rookie Rumble')

    def test_hyphenated_co_op_also_counts(self):
        self.assertTrue(self.matchups.fixture_for('Three Unique Gamers', 'Midwest is Best').coop)

    def test_an_ordinary_week_is_not_coop(self):
        self.assertFalse(self.matchups.fixture_for('Shadow Cartel', 'Midwest is Best').coop)

    def test_the_flag_string_after_the_colon_is_never_read(self):
        sheet = '\n'.join([
            '"Week 2 - TTP3 Power: xx Co-Op xx","",""',
            '"Division","Away Team","Home Team"',
            '"Hyrule Division","Shadow Cartel","Midwest is Best"',
        ])
        fixture = parse_matchups(sheet, QUIET).fixture_for('Shadow Cartel', 'Midwest is Best')
        self.assertFalse(fixture.coop)

    def test_existing_constructions_default_to_not_coop(self):
        self.assertFalse(Fixture(week=1, away='A', home='B').coop)

    def test_accepts_the_spellings_organisers_use(self):
        for label in ('Week 3 - Coop Info Share', 'Week 5 - Co-op 4x4', 'Week 5 - CO OP 4x4', 'Week 6 - co-op'):
            with self.subTest(label=label):
                self.assertTrue(Fixture(week=1, away='A', home='B', label=label).coop)

    def test_does_not_mistake_words_that_merely_contain_coop(self):
        for label in ('Week 4 - Scoop Showdown', 'Week 4 - Cooper Cup', 'Week 4 - Coopers', 'Week 4 - TTP3 Power'):
            with self.subTest(label=label):
                self.assertFalse(Fixture(week=1, away='A', home='B', label=label).coop)
