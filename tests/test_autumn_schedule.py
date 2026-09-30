"""Reading the Autumn Schedule tab.

The header below is the production tab's, fetched from it:

    Date,Time,Runner 1,Runner 2,,Comms 1,Comms 2,Tracker,,Channel

Two blank spacer columns and no `Game`. The spacers are the point of resolving
columns by header: `Channel` is at index 9, and any count of columns finds the
wrong cell.
"""

import logging
import unittest

from ttpbot.autumn.schedule import parse_schedule, schedule_is_readable

HEADER = 'Date,Time,Runner 1,Runner 2,,Comms 1,Comms 2,Tracker,,Channel'


def sheet(*rows):
    return '\n'.join((HEADER,) + rows)


class QuietLog(logging.Logger):
    def __init__(self):
        super().__init__('quiet')
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


class ReadingTheTab(unittest.TestCase):
    def test_the_production_header_alone_is_an_empty_schedule(self):
        # What the tab holds today. Readable, and no races -- which is a
        # different thing from a response that is not the tab.
        found = parse_schedule(HEADER)
        self.assertTrue(found.readable)
        self.assertEqual(found.rows, [])
        self.assertEqual(found.bad, [])

    def test_a_sign_in_page_is_not_a_schedule(self):
        # Google answers 200 with HTML when a share link lapses. Believing that
        # is an empty schedule cancels every race on the tab.
        self.assertFalse(schedule_is_readable('<html><body>Sign in</body></html>'))
        self.assertFalse(parse_schedule('<html>Sign in</html>').readable)
        self.assertFalse(parse_schedule('').readable)

    def test_a_row_is_read_whole(self):
        found = parse_schedule(sheet(
            '10/02/2026,10:00 PM,(46) ISUMatt,(7) chessjerk,,Bogie,Merks,f451,,z1rracing',
        ))
        self.assertTrue(found.readable)
        row, = found.rows
        self.assertEqual(row.runner_one, '(46) ISUMatt')
        self.assertEqual(row.runner_two, '(7) chessjerk')
        self.assertEqual(row.crew, ['Bogie', 'Merks', 'f451'])
        # Index 9, past two blank headers. This is the cell a positional parser
        # got wrong before.
        self.assertEqual(row.channel, 'z1rracing')
        self.assertEqual(row.line, 2)

    def test_the_names_are_carried_exactly_as_the_sheet_spells_them(self):
        # The ranking prefix and the sheet's spelling both stay. Stripping either
        # here would be a second opinion about who somebody is, and the matcher
        # holds the only one -- it has the engine's aliases.
        row, = parse_schedule(sheet(
            '10/02/2026,10:00 PM,(12) RhjnoHero,(14) Pool Float,,,,,,',
        )).rows
        self.assertEqual(row.runner_one, '(12) RhjnoHero')
        self.assertEqual(row.runner_two, '(14) Pool Float')

    def test_the_time_is_eastern_and_knows_about_dst(self):
        october, november = parse_schedule(sheet(
            '10/02/2026,10:00 PM,A,B,,,,,,',
            '11/20/2026,10:00 PM,C,D,,,,,,',
        )).rows
        self.assertEqual(october.at.isoformat(), '2026-10-02T22:00:00-04:00')
        self.assertEqual(november.at.isoformat(), '2026-11-20T22:00:00-05:00')

    def test_midnight_and_a_24_hour_clock_both_read(self):
        rows = parse_schedule(sheet(
            '10/02/2026,12:00 AM,A,B,,,,,,',
            '10/02/2026,23:30,C,D,,,,,,',
            '10/02/2026,9:05:00 PM,E,F,,,,,,',
        )).rows
        self.assertEqual([row.at.hour for row in rows], [0, 23, 21])
        self.assertEqual(rows[2].at.minute, 5)


class RowsItCannotUse(unittest.TestCase):
    def test_a_blank_trailing_line_is_silent(self):
        # Every spreadsheet export ends with one, and complaining once a minute
        # buries the rows that matter.
        log = QuietLog()
        found = parse_schedule(sheet('10/02/2026,10:00 PM,A,B,,,,,,', ',,,,,,,,,'), log)
        self.assertEqual(len(found.rows), 1)
        self.assertEqual(found.bad, [])
        self.assertEqual(log.warnings, [])

    def test_an_unreadable_time_is_reported_not_dropped(self):
        # Two racers think they are scheduled. Silence is worse than a complaint.
        log = QuietLog()
        found = parse_schedule(sheet('10/02/2026,whenever,A,B,,,,,,'), log)
        self.assertEqual(found.rows, [])
        bad, = found.bad
        self.assertEqual(bad.line, 2)
        self.assertIn('whenever', bad.reason)
        self.assertTrue(any('row 2' in warned for warned in log.warnings))

    def test_a_row_missing_a_runner_is_reported(self):
        found = parse_schedule(sheet('10/02/2026,10:00 PM,A,,,,,,,'))
        self.assertEqual(found.rows, [])
        self.assertIn('both runners', found.bad[0].reason)

    def test_one_bad_row_does_not_cost_the_good_ones(self):
        found = parse_schedule(sheet(
            '10/02/2026,10:00 PM,A,B,,,,,,',
            'not a date,10:00 PM,C,D,,,,,,',
            '10/04/2026,8:00 PM,E,F,,,,,,',
        ))
        self.assertEqual([row.line for row in found.rows], [2, 4])
        self.assertEqual([bad.line for bad in found.bad], [3])

    def test_a_header_missing_a_runner_column_is_refused_outright(self):
        # Never fall back to fixed positions. Reading the wrong columns is how a
        # race is built from the wrong cells, and no room beats a wrong one.
        log = QuietLog()
        found = parse_schedule('Date,Time,Runner 1,,Comms 1\n10/02/2026,10:00 PM,A,B,C', log)
        self.assertFalse(found.readable)
        self.assertEqual(found.rows, [])
        self.assertTrue(any('unusable' in warned for warned in log.warnings))

    def test_the_old_single_comms_header_still_reads(self):
        # The sheet has been reshaped once already, and it can be again without a
        # synchronised deploy.
        row, = parse_schedule(
            'Date,Time,Runner one,Runner two,Comms,Tracker,Channel\n'
            '10/02/2026,10:00 PM,A,B,Bogie,f451,z1rracing'
        ).rows
        self.assertEqual(row.comms_one, 'Bogie')
        self.assertEqual(row.channel, 'z1rracing')


class Ordering(unittest.TestCase):
    def test_rows_come_back_in_time_order_whatever_order_they_were_added(self):
        # This is what separates the grand final from its reset: two rows for the
        # same pair, and the earlier one is the final.
        found = parse_schedule(sheet(
            '10/05/2026,9:00 PM,Bogie,Merks,,,,,,',
            '10/04/2026,9:00 PM,Bogie,Merks,,,,,,',
        ))
        self.assertEqual([row.line for row in found.in_time_order()], [3, 2])

    def test_a_second_row_for_one_pair_is_kept(self):
        # A reschedule appears as another row, and so does a scheduled reset.
        # Collapsing them here throws away the only thing that tells them apart.
        found = parse_schedule(sheet(
            '10/04/2026,9:00 PM,Bogie,Merks,,,,,,',
            '10/05/2026,9:00 PM,Bogie,Merks,,,,,,',
        ))
        self.assertEqual(len(found.rows), 2)


if __name__ == '__main__':
    unittest.main()
