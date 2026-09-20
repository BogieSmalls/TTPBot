"""The League results recorder: what it submits, and what it refuses to."""
import tempfile
import unittest
from unittest.mock import MagicMock

from ttpbot.state import DestinationStateStore

from ttpbot.league.results import (
    ResultsRecorder,
    Submission,
    state_key,
    entrants_by_racer,
    finish_clock,
    pairings_from,
    submissions_for,
)
from ttpbot.league.roster import load_roster

ROSTER = load_roster()

SCHEDULE_HEADER = [
    'Date', 'Time', 'Game', 'Runner 1', 'Runner 2', '',
    'Comms 1', 'Comms 2', 'Tracker', '', 'Channel', 'Booth',
]

#: The three co-op rooms of 18 September, as racetime reported them. Real
#: payloads: this is the run that was reviewed before the feature was built.
ROOMS = [
    {
        'name': 'z1r/obedient-rope-9691',
        'status': {'value': 'finished'},
        'started_at': '2026-09-18T23:03:00.000Z',
        'ended_at': '2026-09-19T00:18:07.470Z',
        'entrants': [
            {'user': {'id': 'r1', 'name': 'Moneymerks'}, 'status': {'value': 'done'},
             'finish_time': 'P0DT00H59M00.401547S'},
            {'user': {'id': 'r2', 'name': 'thomjay'}, 'status': {'value': 'done'},
             'finish_time': 'P0DT01H00M00.885371S'},
            {'user': {'id': 'r3', 'name': 'Sigil711'}, 'status': {'value': 'done'},
             'finish_time': 'P0DT01H08M18.941119S'},
            {'user': {'id': 'r4', 'name': 'ISUMatt'}, 'status': {'value': 'done'},
             'finish_time': 'P0DT01H12M09.214526S'},
        ],
    },
]

ARCHIVE_ROWS = [
    SCHEDULE_HEADER,
    ['9/18/2026', '7:00:00 PM', '2', '(TBC) ISUMatt', '(TML) Merks', ''],
    ['9/18/2026', '7:00:00 PM', '2', '(TBC) Sigil', '(TML) Thomjay', ''],
]


def _named(name):
    """The racer the roster knows by that sheet name."""
    return ROSTER.resolve(name)


class FinishClockTest(unittest.TestCase):
    def test_reads_a_racetime_duration(self):
        self.assertEqual(finish_clock('P0DT01H14M39.189022S'), '1:14:39')
        self.assertEqual(finish_clock('P0DT00H55M46.274536S'), '0:55:46')

    def test_truncates_rather_than_rounds(self):
        # The racetime results page says 59:00, so the form should too.
        self.assertEqual(finish_clock('P0DT00H59M00.901547S'), '0:59:00')

    def test_refuses_anything_it_cannot_read(self):
        for value in (None, '', 'an hour', 'PT', 25):
            self.assertIsNone(finish_clock(value))


class PairingsTest(unittest.TestCase):
    def test_reads_pairings_by_column_name(self):
        pairings = pairings_from(ARCHIVE_ROWS, ROSTER)
        self.assertEqual(
            [(p.one.sheet_name, p.two.sheet_name) for p in pairings],
            [('ISUMatt', 'Merks'), ('Sigil', 'Thomjay')],
        )

    def test_skips_a_row_naming_someone_unknown(self):
        rows = list(ARCHIVE_ROWS) + [
            ['9/20/2026', '8:00:00 PM', '1', '(TBC) ISUMatt', '(XXX) Nobody', ''],
        ]
        self.assertEqual(len(pairings_from(rows, ROSTER)), 2)

    def test_empty_when_the_tab_has_no_runner_columns(self):
        self.assertEqual(pairings_from([['Date', 'Time']], ROSTER), [])
        self.assertEqual(pairings_from([], ROSTER), [])


class EntrantMatchingTest(unittest.TestCase):
    def test_matches_on_racetime_id_not_on_name(self):
        # The sheet's "Merks" is racetime's "Moneymerks": matching on names
        # would drop this racer, and with them the whole pairing.
        merks = _named('Merks')
        race = {'entrants': [
            {'user': {'id': merks.racetime_id, 'name': 'someone else entirely'},
             'status': {'value': 'done'}, 'finish_time': 'P0DT01H00M00S'},
        ]}
        seats = entrants_by_racer(race, ROSTER)
        self.assertIn('Merks', seats)

    def test_falls_back_to_the_name_when_there_is_no_id(self):
        race = {'entrants': [
            {'user': {'name': 'ISUMatt'}, 'status': {'value': 'done'},
             'finish_time': 'P0DT01H00M00S'},
        ]}
        self.assertIn('ISUMatt', entrants_by_racer(race, ROSTER))


class SubmissionsTest(unittest.TestCase):
    def test_a_coop_room_produces_one_row_per_pairing(self):
        pairings = pairings_from(ARCHIVE_ROWS, ROSTER)
        submissions, problems = submissions_for(ROOMS[0], pairings, ROSTER)
        self.assertEqual(problems, [])
        self.assertEqual(
            [s.describe() for s in submissions],
            [
                '(TML) Merks 0:59:00 beat (TBC) ISUMatt 1:12:09',
                '(TML) Thomjay 1:00:00 beat (TBC) Sigil 1:08:18',
            ],
        )

    def test_a_later_game_between_the_same_racers_is_not_this_race(self):
        # The bug this caught in production: the same matchup plays again, the
        # row is sitting in Schedule unplayed, and every racer in it is an
        # entrant here -- so it was filed as a result of tonight's race, with
        # the pairings of another night.
        rows = list(ARCHIVE_ROWS) + [
            ['9/25/2026', '7:00:00 PM', '1', '(TBC) ISUMatt', '(TML) Thomjay', ''],
            ['9/25/2026', '7:00:00 PM', '1', '(TBC) Sigil', '(TML) Merks', ''],
        ]
        submissions, _ = submissions_for(ROOMS[0], pairings_from(rows, ROSTER), ROSTER)
        self.assertEqual(
            [s.describe() for s in submissions],
            [
                '(TML) Merks 0:59:00 beat (TBC) ISUMatt 1:12:09',
                '(TML) Thomjay 1:00:00 beat (TBC) Sigil 1:08:18',
            ],
        )

    def test_a_race_with_no_start_time_is_refused(self):
        race = dict(ROOMS[0])
        race.pop('started_at')
        race.pop('opened_at', None)
        submissions, problems = submissions_for(
            race, pairings_from(ARCHIVE_ROWS, ROSTER), ROSTER)
        self.assertEqual(submissions, [])
        self.assertEqual(len(problems), 1)

    def test_ignores_pairings_that_did_not_race_here(self):
        rows = list(ARCHIVE_ROWS) + [
            ['9/20/2026', '8:00:00 PM', '1', '(MiB) jessandy8', '(Tek) Bogie', ''],
        ]
        submissions, _ = submissions_for(ROOMS[0], pairings_from(rows, ROSTER), ROSTER)
        self.assertEqual(len(submissions), 2)

    def test_a_dnf_loses_and_files_no_time(self):
        race = {
            'started_at': '2026-09-18T23:03:00.000Z',
            'entrants': [
                {'user': {'name': 'ISUMatt'}, 'status': {'value': 'done'},
                 'finish_time': 'P0DT01H00M00S'},
                {'user': {'name': 'Merks'}, 'status': {'value': 'dnf'},
                 'finish_time': None},
            ],
        }
        submissions, problems = submissions_for(
            race, pairings_from(ARCHIVE_ROWS, ROSTER), ROSTER)
        self.assertEqual(problems, [])
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0].winner, '(TBC) ISUMatt')
        self.assertTrue(submissions[0].loser_dnf)
        self.assertEqual(submissions[0].form_data()['entry.1704083761'], '')

    def test_a_race_nobody_finished_is_reported_not_guessed(self):
        race = {
            'started_at': '2026-09-18T23:03:00.000Z',
            'entrants': [
                {'user': {'name': 'ISUMatt'}, 'status': {'value': 'dnf'}, 'finish_time': None},
                {'user': {'name': 'Merks'}, 'status': {'value': 'dnf'}, 'finish_time': None},
            ],
        }
        submissions, problems = submissions_for(
            race, pairings_from(ARCHIVE_ROWS, ROSTER), ROSTER)
        self.assertEqual(submissions, [])
        self.assertEqual(len(problems), 1)

    def test_form_fields_carry_the_exact_dropdown_options(self):
        submission = Submission(
            winner='(TML) Merks', winner_time='0:59:00',
            loser='(TBC) ISUMatt', loser_dnf=False, loser_time='1:12:09',
        )
        self.assertEqual(submission.form_data(), {
            'entry.2010877951': '(TML) Merks',
            'entry.1814337419': '0:59:00',
            'entry.309845690': '(TBC) ISUMatt',
            'entry.1202898048': 'No',
            'entry.1704083761': '1:12:09',
        })


class StateKeyTest(unittest.TestCase):
    def test_racetime_timestamps_are_keyed_in_a_form_python_can_read(self):
        # Production runs 3.10, where fromisoformat refuses a trailing "Z" --
        # and the state store parses this timestamp when it validates the key.
        self.assertEqual(
            state_key('2026-09-19T00:18:07.470Z', 'obedient-rope-9691', 0),
            '2026-09-19T00:18:07.470+00:00|obedient-rope-9691-0',
        )

    def test_an_unreadable_time_is_refused_here_not_after_posting(self):
        with self.assertRaises(ValueError):
            state_key('last tuesday', 'obedient-rope-9691', 0)


class RecorderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.posted = []
        self.accept = True
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        # The real store, not a stand-in: it is the thing that validates the
        # key, and a permissive fake hid a production-only failure once.
        self.store = DestinationStateStore(
            'league_results.json', 'https://racetime.gg|z1r', 'league_results',
            data_dir=self._dir.name,
        )

    def _recorder(self):
        async def requester(url, data):
            self.posted.append(data)
            return self.accept

        async def fetcher(url):
            return ARCHIVE_ROWS if 'gid=1495655076' in url else [SCHEDULE_HEADER]

        return ResultsRecorder(
            roster=ROSTER, store=self.store, logger=MagicMock(),
            archives_url='https://sheet/export?gid=1495655076',
            schedule_url='https://sheet/export?gid=2033319762',
            requester=requester, fetcher=fetcher,
        )

    async def test_submits_every_pairing_in_the_room(self):
        sent = await self._recorder().record(ROOMS[0])
        self.assertEqual(sent, 2)
        self.assertEqual(
            [p['entry.2010877951'] for p in self.posted],
            ['(TML) Merks', '(TML) Thomjay'],
        )

    async def test_a_second_run_posts_nothing(self):
        recorder = self._recorder()
        await recorder.record(ROOMS[0])
        self.posted.clear()
        self.assertEqual(await recorder.record(ROOMS[0]), 0)
        self.assertEqual(self.posted, [])

    async def test_a_failed_post_stops_and_is_retried_next_time(self):
        self.accept = False
        recorder = self._recorder()
        self.assertEqual(await recorder.record(ROOMS[0]), 0)
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.store.load(), {})

        # The form recovers; the run resumes and nothing is duplicated.
        self.accept = True
        self.posted.clear()
        self.assertEqual(await recorder.record(ROOMS[0]), 2)
        self.assertEqual(len(self.posted), 2)

    async def test_a_cancelled_room_records_nothing(self):
        race = dict(ROOMS[0], status={'value': 'cancelled'})
        self.assertEqual(await self._recorder().record(race), 0)
        self.assertEqual(self.posted, [])

    async def test_unreadable_sheets_record_nothing(self):
        async def fetcher(url):
            return None

        recorder = self._recorder()
        recorder._fetcher = fetcher
        self.assertEqual(await recorder.record(ROOMS[0]), 0)
        self.assertEqual(self.posted, [])


if __name__ == '__main__':
    unittest.main()
