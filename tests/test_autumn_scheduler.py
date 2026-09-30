"""The Autumn race night, end to end, on fixtures.

One match all the way through: scheduled, mirrored, rescheduled, a room at T-30,
and the two finals. Nothing here touches Google, racetime or Discord.

The bug this design exists to avoid is the League's: it keys room state by start
time, so a postponed race is a cache miss and gets a *second* room. Here the key
is the match id, which does not move when the time does -- so the reschedule
tests below are checking that a room is found, not that a workaround fires.
"""

import asyncio
from datetime import datetime, timedelta
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ttpbot.autumn.engine import NOT_RECORDED, RECORDED, UNCONFIRMED, Written
from ttpbot.autumn.scheduler import (
    BOOTH_WAKE_MINUTES_BEFORE,
    ROOM_OPEN_MINUTES_BEFORE,
    AutumnScheduler,
)
from ttpbot.autumn.schedule import parse_schedule
from ttpbot.config import TIMEZONE
from ttpbot.state import DestinationStateStore

DESTINATION = 'https://racetime.gg|z1r'
HEADER = 'Date,Time,Runner 1,Runner 2,,Comms 1,Comms 2,Tracker,,Channel'
ROOM = 'https://racetime.gg/z1r/fancy-mario-1234'

START = datetime(2026, 10, 2, 22, 0, tzinfo=TIMEZONE)
LATER = datetime(2026, 10, 4, 21, 0, tzinfo=TIMEZONE)


def at(start, minutes_before):
    return start - timedelta(minutes=minutes_before)


def sheet(*rows):
    return '\n'.join((HEADER,) + rows)


def row(start, one, two, channel=''):
    return '{},{},{},{},,{},,,,{}'.format(
        start.strftime('%m/%d/%Y'), start.strftime('%I:%M %p'), one, two,
        'Bogie' if channel else '', channel)


class Log(logging.Logger):
    def __init__(self):
        super().__init__('autumn')
        self.warnings = []
        self.errors = []

    def warning(self, msg, *args, **kw):
        self.warnings.append(msg % args if args else msg)

    def error(self, msg, *args, **kw):
        self.errors.append(msg % args if args else msg)

    def info(self, msg, *args, **kw):
        pass


class FakeSource:
    configured = True

    def __init__(self, csv_text):
        self.csv_text = csv_text

    async def rows(self, now):
        return parse_schedule(self.csv_text)


class FakeEngine:
    """The engine, as far as the scheduler can tell."""

    def __init__(self, matches, aliases=None, outcome=RECORDED):
        self.matches = matches
        self.aliases = aliases or {}
        self.outcome = outcome
        self.mirrored = []

    async def draw(self):
        return {
            'drawn': True,
            'matches': [dict(match, id=match_id)
                        for match_id, match in self.matches.items()],
            'aliases': self.aliases,
        }

    async def mirror_time(self, match_id, when):
        self.mirrored.append((match_id, when.isoformat()))
        if callable(self.outcome):
            return self.outcome(match_id, when)
        return Written(self.outcome, revision=1, detail='fixture')


def ready(a, b):
    return {'a': a, 'b': b, 'state': 'ready'}


def run(coro):
    return asyncio.run(coro)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.log = Log()
        self.opened = []
        self.woken = []
        self.announced = []

    def store(self, kind):
        return DestinationStateStore(
            '{}.json'.format(kind), DESTINATION, kind, data_dir=self.root)

    def scheduler(self, csv_text, matches, aliases=None, engine=None,
                  open_room=True, wake=True):
        async def opener(race, row):
            self.opened.append(race.match_id)
            return ROOM

        async def waker(race, channel):
            self.woken.append((race.match_id, channel))

        async def announcer(race, row, url):
            self.announced.append((race.match_id, url))

        return AutumnScheduler(
            source=FakeSource(csv_text),
            engine=engine or FakeEngine(matches, aliases),
            logger=self.log,
            bindings_store=self.store('autumn_bindings'),
            created_store=self.store('autumn_created_races'),
            mirrored_store=self.store('autumn_mirrored_times'),
            open_room=opener if open_room else None,
            wake_booth=waker if wake else None,
            announce=announcer,
        )


class OneMatchEndToEnd(SchedulerTests):
    def test_scheduled_mirrored_woken_opened_announced(self):
        it = self.scheduler(
            sheet(row(START, '(46) ISUMatt', '(7) chessjerk', 'z1rracing')),
            {'W1-1': ready('ISUMatt', 'chessjerk')},
        )

        # Hours out: the time reaches the engine, and nothing else happens.
        run(it.tick(START - timedelta(hours=5)))
        self.assertEqual(it.engine.mirrored, [('W1-1', START.isoformat())])
        self.assertEqual(self.opened, [])
        self.assertEqual(self.woken, [])
        # And the row is bound to its match, scoped to the competition.
        self.assertEqual(
            list(it.bindings.values()), ['W1-1'])
        self.assertTrue(all(k.startswith('autumn|') for k in it.bindings))

        # T-35: the booth is asked to wake, the room is still not open.
        run(it.tick(at(START, BOOTH_WAKE_MINUTES_BEFORE)))
        self.assertEqual(self.woken, [('W1-1', 'z1rracing')])
        self.assertEqual(self.opened, [])

        # T-30: the room.
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['W1-1'])
        self.assertEqual(self.announced, [('W1-1', ROOM)])

        # And every tick after that changes nothing.
        run(it.tick(at(START, 10)))
        run(it.tick(START))
        self.assertEqual(self.opened, ['W1-1'])
        self.assertEqual(self.woken, [('W1-1', 'z1rracing')])
        self.assertEqual(len(it.engine.mirrored), 1, 'the time was sent once')

    def test_the_time_is_only_sent_once_unless_it_changes(self):
        it = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk')),
            {'W1-1': ready('ISUMatt', 'chessjerk')},
        )
        for hours in (5, 4, 3):
            run(it.tick(START - timedelta(hours=hours)))
        self.assertEqual(len(it.engine.mirrored), 1)

    def test_a_name_off_the_sheet_reaches_its_match_through_the_aliases(self):
        # `RhjnoHero` does not flatten to `RhinoHero` -- it is a typo -- and
        # `Pool Float` is a different name. Without the engine's aliases this row
        # resolves to nobody and no room opens.
        it = self.scheduler(
            sheet(row(START, '(12) RhjnoHero', '(14) Pool Float', 'z1rracing')),
            {'W1-2': ready('RhinoHero', 'poolfloatg')},
            aliases={'RhjnoHero': 'RhinoHero', 'Pool Float': 'poolfloatg'},
        )
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['W1-2'])

    def test_without_the_aliases_it_is_reported_rather_than_guessed(self):
        it = self.scheduler(
            sheet(row(START, '(12) RhjnoHero', '(14) Pool Float')),
            {'W1-2': ready('RhinoHero', 'poolfloatg')},
        )
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])
        self.assertTrue(
            any('no match' in w for w in self.log.warnings), self.log.warnings)


class Rescheduling(SchedulerTests):
    def test_a_moved_race_keeps_its_room(self):
        # The League's bug, which this cannot have: its room key is the start
        # time, so a postponed race misses the cache and gets a second room. Here
        # the key is the match.
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        it = self.scheduler(sheet(row(START, 'ISUMatt', 'chessjerk')), matches)
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['W1-1'])

        # Now the sheet carries a second row: the race was moved two days on.
        moved = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk'),
                  row(LATER, 'ISUMatt', 'chessjerk')),
            matches,
        )
        # Same stores, so it remembers the room it made.
        self.assertIn('autumn|W1-1', moved.created)
        run(moved.tick(at(LATER, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['W1-1'], 'no second room')

    def test_the_new_time_is_mirrored(self):
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        it = self.scheduler(sheet(row(START, 'ISUMatt', 'chessjerk')), matches)
        run(it.tick(START - timedelta(hours=5)))

        moved = self.scheduler(sheet(row(LATER, 'ISUMatt', 'chessjerk')), matches)
        run(moved.tick(LATER - timedelta(hours=5)))
        self.assertEqual(moved.engine.mirrored, [('W1-1', LATER.isoformat())])

    def test_a_row_keeps_the_match_it_was_given_across_a_restart(self):
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        it = self.scheduler(sheet(row(START, 'ISUMatt', 'chessjerk')), matches)
        run(it.tick(START - timedelta(hours=5)))
        remembered = dict(it.bindings)

        restarted = self.scheduler(sheet(row(START, 'ISUMatt', 'chessjerk')), matches)
        self.assertEqual(restarted.bindings, remembered)


class TheTwoFinals(SchedulerTests):
    def finals(self, reset_state='not-needed', final_state='ready'):
        return {
            'GF-1': {'a': 'Bogie', 'b': 'Merks', 'state': final_state},
            'GF-2': {'a': 'Bogie' if reset_state != 'not-needed' else None,
                     'b': 'Merks' if reset_state != 'not-needed' else None,
                     'state': reset_state},
        }

    def test_both_scheduled_at_once_opens_only_the_final(self):
        # The finalists agree both times in one go, because nobody wants to be
        # arranging a second race at one in the morning. The reset is scheduled
        # and not raceable, so its time is mirrored and its room is not opened.
        it = self.scheduler(
            sheet(row(START, 'Bogie', 'Merks'),
                  row(START + timedelta(hours=2), 'Bogie', 'Merks')),
            self.finals(),
        )
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['GF-1'])
        self.assertEqual(
            sorted(match for match, _ in it.engine.mirrored), ['GF-1', 'GF-2'])

    def test_the_reset_opens_once_the_final_calls_for_it(self):
        reset_at = START + timedelta(hours=2)
        csv_text = sheet(row(START, 'Bogie', 'Merks'), row(reset_at, 'Bogie', 'Merks'))

        it = self.scheduler(csv_text, self.finals())
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['GF-1'])

        # The final is played and the losers-bracket side won it, so GF-2 is on.
        played = self.scheduler(
            csv_text, self.finals(reset_state='ready', final_state='played'))
        run(played.tick(at(reset_at, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['GF-1', 'GF-2'])

    def test_a_final_that_settles_it_opens_no_reset(self):
        # `not-needed` after the final has been played means never, and this is
        # the case where believing otherwise opens a room for a tournament that
        # is already over.
        reset_at = START + timedelta(hours=2)
        it = self.scheduler(
            sheet(row(START, 'Bogie', 'Merks'), row(reset_at, 'Bogie', 'Merks')),
            self.finals(reset_state='not-needed', final_state='played'),
        )
        run(it.tick(at(reset_at, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])

    def test_one_row_and_a_played_final_does_not_become_the_reset(self):
        # The row is the time two people agreed for a race that has now been run.
        # Rebinding it to GF-2 would open the reset at a time nobody proposed.
        csv_text = sheet(row(START, 'Bogie', 'Merks'))
        it = self.scheduler(csv_text, self.finals())
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['GF-1'])

        played = self.scheduler(
            csv_text, self.finals(reset_state='ready', final_state='played'))
        run(played.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['GF-1'], 'the reset must not reuse that row')


class WhenThingsGoWrong(SchedulerTests):
    def test_an_unreadable_schedule_opens_nothing(self):
        it = self.scheduler(
            '<html>Sign in</html>', {'W1-1': ready('ISUMatt', 'chessjerk')})
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])

    def test_an_unconfirmed_mirror_is_retried_next_tick(self):
        # Because it may never have landed, and the write is idempotent.
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        engine = FakeEngine(matches, outcome=UNCONFIRMED)
        it = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk')), matches, engine=engine)
        run(it.tick(START - timedelta(hours=5)))
        run(it.tick(START - timedelta(hours=4)))
        self.assertEqual(len(engine.mirrored), 2)
        self.assertTrue(any('not confirmed' in w for w in self.log.warnings))

    def test_a_refused_mirror_does_not_stop_the_room(self):
        # The sheet owns the time. A mirror the engine will not take is worth
        # saying loudly and is not a reason to leave two racers without a room.
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        it = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk')), matches,
            engine=FakeEngine(matches, outcome=NOT_RECORDED))
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['W1-1'])

    def test_a_race_long_past_is_left_alone(self):
        it = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk')),
            {'W1-1': ready('ISUMatt', 'chessjerk')})
        run(it.tick(START + timedelta(hours=3)))
        self.assertEqual(self.opened, [])

    def test_unreadable_state_stops_it_rather_than_guessing(self):
        # Acting on state we know is missing is how a second room gets made for a
        # race that already has one.
        (self.root / 'autumn_bindings.json').write_text('{not json', encoding='utf-8')
        it = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk')),
            {'W1-1': ready('ISUMatt', 'chessjerk')})
        self.assertTrue(it.stopped)
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])
        self.assertTrue(any('stopped' in e for e in self.log.errors))

    def test_no_engine_is_off_rather_than_broken(self):
        it = AutumnScheduler(
            source=FakeSource(sheet(row(START, 'A', 'B'))),
            engine=None, logger=self.log)
        self.assertFalse(it.configured)
        run(it.tick(START))
        self.assertEqual(self.log.errors, [])

    def test_a_bad_row_is_reported(self):
        it = self.scheduler(
            sheet('10/02/2026,whenever,ISUMatt,chessjerk,,,,,,'),
            {'W1-1': ready('ISUMatt', 'chessjerk')})
        run(it.tick(START - timedelta(hours=5)))
        self.assertTrue(
            any('could not be read' in w for w in self.log.warnings),
            self.log.warnings)


if __name__ == '__main__':
    unittest.main()
