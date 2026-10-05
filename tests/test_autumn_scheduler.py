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
from ttpbot.state import UNCERTAIN_RACE, DestinationStateStore

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
            announced_store=self.store('autumn_sent_webhooks'),
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

    def test_a_refused_mirror_holds_the_match(self):
        # The sheet owns *when* a match is. It does not get to say that a match
        # which is over is happening tonight -- and a refusal is the engine saying
        # exactly that. `time` refuses for three reasons and every one means no
        # room: the match is not in the bracket, it is a bye, or it has already
        # been raced and won.
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        it = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk')), matches,
            engine=FakeEngine(matches, outcome=NOT_RECORDED))
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [], 'a refused mirror must not open a room')
        self.assertTrue(
            any('refused' in e for e in self.log.errors), self.log.errors)

    def test_a_race_the_engine_says_is_already_won_gets_no_room(self):
        # The concrete case behind the rule above. The engine answers
        # "W1-1 has already been raced and won by ISUMatt" with a 409, and a room
        # opened on that is a room for a tournament that has moved on.
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}

        def refuse(match_id, when):
            return Written(
                NOT_RECORDED,
                detail='{} has already been raced and won by ISUMatt'.format(match_id))

        it = self.scheduler(
            sheet(row(START, 'ISUMatt', 'chessjerk')), matches,
            engine=FakeEngine(matches, outcome=refuse))
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])
        self.assertTrue(any('already been raced' in e for e in self.log.errors))

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


class OnlyOneRoomEver(SchedulerTests):
    """The one mistake a race night cannot absorb.

    Creating first and recording afterwards means a creation whose answer is lost
    leaves no trace, and the next tick makes a second room. So the reservation
    goes to disk *before* the opener is called.
    """

    def raising_scheduler(self, matches, boom):
        async def opener(race, row):
            self.opened.append(race.match_id)
            raise boom

        async def waker(race, channel):
            self.woken.append((race.match_id, channel))

        return AutumnScheduler(
            source=FakeSource(sheet(row(START, 'ISUMatt', 'chessjerk', 'z1rracing'))),
            engine=FakeEngine(matches),
            logger=self.log,
            bindings_store=self.store('autumn_bindings'),
            created_store=self.store('autumn_created_races'),
            mirrored_store=self.store('autumn_mirrored_times'),
            announced_store=self.store('autumn_sent_webhooks'),
            open_room=opener,
            wake_booth=waker,
        )

    def test_a_creation_whose_answer_is_lost_is_never_attempted_twice(self):
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        it = self.raising_scheduler(matches, RuntimeError('socket went away'))
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))

        # It tried once, and the attempt is on disk as uncertain.
        self.assertEqual(self.opened, ['W1-1'])
        self.assertEqual(it.created['autumn|W1-1'], UNCERTAIN_RACE)
        self.assertEqual(
            self.store('autumn_created_races').load()['autumn|W1-1'], UNCERTAIN_RACE)

        # A later tick, and a whole new process, both leave it alone.
        run(it.tick(at(START, 20)))
        self.assertEqual(self.opened, ['W1-1'], 'no second attempt in this process')

        restarted = self.raising_scheduler(matches, RuntimeError('again'))
        run(restarted.tick(at(START, 15)))
        self.assertEqual(self.opened, ['W1-1'], 'and none after a restart')
        self.assertTrue(
            any('never confirmed' in e for e in self.log.errors), self.log.errors)

    def test_an_uncertain_room_is_not_announced(self):
        # There is no URL to announce, and inventing one is worse than silence.
        it = self.raising_scheduler(
            {'W1-1': ready('ISUMatt', 'chessjerk')}, RuntimeError('lost'))
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.announced, [])

    def test_a_definite_failure_releases_the_reservation(self):
        # The opener's contract: falsey means it definitely did not create
        # anything. Holding the reservation then would block the match for good
        # over a transient racetime error.
        matches = {'W1-1': ready('ISUMatt', 'chessjerk')}
        attempts = []

        async def opener(race, row):
            attempts.append(race.match_id)
            return None if len(attempts) == 1 else ROOM

        it = AutumnScheduler(
            source=FakeSource(sheet(row(START, 'ISUMatt', 'chessjerk'))),
            engine=FakeEngine(matches), logger=self.log,
            bindings_store=self.store('autumn_bindings'),
            created_store=self.store('autumn_created_races'),
            mirrored_store=self.store('autumn_mirrored_times'),
            announced_store=self.store('autumn_sent_webhooks'),
            open_room=opener,
        )
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertNotIn('autumn|W1-1', it.created)
        run(it.tick(at(START, 25)))
        self.assertEqual(attempts, ['W1-1', 'W1-1'])
        self.assertEqual(it.created['autumn|W1-1'], ROOM)


class AFailedSaveStopsTheTick(SchedulerTests):
    def unwritable(self, kind):
        """A store that loads but will not save."""
        store = self.store(kind)
        original = store.save

        def refuse(entries):
            raise OSError('read-only file system')

        store.save = refuse
        store.load = original and store.load
        return store

    def scheduler_with(self, broken_kind, matches):
        async def opener(race, row):
            self.opened.append(race.match_id)
            return ROOM

        stores = {}
        for kind in ('autumn_bindings', 'autumn_created_races',
                     'autumn_mirrored_times', 'autumn_sent_webhooks'):
            stores[kind] = (
                self.unwritable(kind) if kind == broken_kind else self.store(kind))

        return AutumnScheduler(
            source=FakeSource(sheet(row(START, 'ISUMatt', 'chessjerk'))),
            engine=FakeEngine(matches), logger=self.log,
            bindings_store=stores['autumn_bindings'],
            created_store=stores['autumn_created_races'],
            mirrored_store=stores['autumn_mirrored_times'],
            announced_store=stores['autumn_sent_webhooks'],
            open_room=opener,
        )

    def test_a_binding_that_cannot_be_saved_opens_no_room(self):
        # A room opened against a binding that exists only in memory is a room the
        # next restart cannot account for.
        it = self.scheduler_with(
            'autumn_bindings', {'W1-1': ready('ISUMatt', 'chessjerk')})
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])
        self.assertTrue(it.stopped)

    def test_a_reservation_that_cannot_be_saved_opens_no_room(self):
        # Persisting the intent is the precondition for attempting it, so without
        # that there is no attempt at all.
        it = self.scheduler_with(
            'autumn_created_races', {'W1-1': ready('ISUMatt', 'chessjerk')})
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])
        self.assertTrue(it.stopped)

    def test_being_stopped_ends_the_tick_rather_than_the_race(self):
        # Two races, and the first one breaks the save. The second must not be
        # handled on state nobody can write.
        matches = {
            'W1-1': ready('ISUMatt', 'chessjerk'),
            'W1-2': ready('Bogie', 'Merks'),
        }
        it = self.scheduler_with('autumn_bindings', matches)
        it.source = FakeSource(sheet(
            row(START, 'ISUMatt', 'chessjerk'),
            row(START, 'Bogie', 'Merks'),
        ))
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, [])


class TransientFailuresAreRetried(SchedulerTests):
    def flaky(self, matches, fail_wake=0, fail_announce=0):
        wake_failures = [fail_wake]
        announce_failures = [fail_announce]

        async def opener(race, row):
            self.opened.append(race.match_id)
            return ROOM

        async def waker(race, channel):
            if wake_failures[0]:
                wake_failures[0] -= 1
                raise RuntimeError('control plane asleep')
            self.woken.append((race.match_id, channel))

        async def announcer(race, row, url):
            if announce_failures[0]:
                announce_failures[0] -= 1
                raise RuntimeError('discord 503')
            self.announced.append((race.match_id, url))

        return AutumnScheduler(
            source=FakeSource(sheet(row(START, 'ISUMatt', 'chessjerk', 'z1rracing'))),
            engine=FakeEngine(matches), logger=self.log,
            bindings_store=self.store('autumn_bindings'),
            created_store=self.store('autumn_created_races'),
            mirrored_store=self.store('autumn_mirrored_times'),
            announced_store=self.store('autumn_sent_webhooks'),
            open_room=opener, wake_booth=waker, announce=announcer,
        )

    def test_a_booth_that_would_not_wake_is_asked_again(self):
        # Marking it done before the call meant one transient failure became a
        # permanent omission: the flag said it had been asked, and no later tick
        # asked again.
        it = self.flaky({'W1-1': ready('ISUMatt', 'chessjerk')}, fail_wake=1)
        run(it.tick(at(START, BOOTH_WAKE_MINUTES_BEFORE)))
        self.assertEqual(self.woken, [])
        run(it.tick(at(START, 34)))
        self.assertEqual(self.woken, [('W1-1', 'z1rracing')])
        # And then it stops, rather than waking it every minute.
        run(it.tick(at(START, 33)))
        self.assertEqual(len(self.woken), 1)

    def test_an_announcement_that_failed_is_posted_on_a_later_tick(self):
        # It was only ever attempted at the moment the room was created, so a
        # Discord blip meant nobody was ever told about the race.
        it = self.flaky({'W1-1': ready('ISUMatt', 'chessjerk')}, fail_announce=1)
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.opened, ['W1-1'])
        self.assertEqual(self.announced, [])

        run(it.tick(at(START, 25)))
        self.assertEqual(self.announced, [('W1-1', ROOM)])
        self.assertEqual(self.opened, ['W1-1'], 'and no second room for the retry')

        # Once told, once only.
        run(it.tick(at(START, 20)))
        self.assertEqual(len(self.announced), 1)

    def test_the_announcement_guard_survives_a_restart(self):
        it = self.flaky({'W1-1': ready('ISUMatt', 'chessjerk')})
        run(it.tick(at(START, ROOM_OPEN_MINUTES_BEFORE)))
        self.assertEqual(self.announced, [('W1-1', ROOM)])

        restarted = self.flaky({'W1-1': ready('ISUMatt', 'chessjerk')})
        run(restarted.tick(at(START, 20)))
        self.assertEqual(len(self.announced), 1, 'not announced twice')
        self.assertEqual(self.opened, ['W1-1'], 'and no second room')


if __name__ == '__main__':
    unittest.main()

class EngineAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_engine_holds_then_authorizes_one_room_and_restart_recovers_a_lost_creation(self):
        await self._run_series(two_zero=False)

    async def test_two_zero_never_opens_game_three_and_sheet_can_remove_unused_game(self):
        await self._run_series(two_zero=True)

    async def _run_series(self, two_zero):
        import json
        import os
        import subprocess
        from ttpbot.autumn.engine import AutumnEngine
        engine_root = os.environ.get('Z1RR_ENGINE_DIR')
        if not engine_root:
            self.skipTest('set Z1RR_ENGINE_DIR for real engine room tests')
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(engine_root).resolve()
        clock=Path(temp.name)/'clock.txt';clock.write_text(at(START,25).isoformat())
        code='''
import {readFileSync} from 'node:fs';
import {createEngineService} from SERVICE;
import {createTournamentStore} from STORE;
import {createTournamentWriter} from WRITER;
const storeFor=event=>createTournamentStore({event,dir:DIR});
const writer=createTournamentWriter({storeFor,now:()=>new Date(readFileSync(CLOCK,'utf8'))});
const server=createEngineService({token:'scratch',storeFor,writer});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
'''
        for key, value in {'SERVICE':(root/'src/service.mjs').as_uri(),'STORE':(root/'src/store.mjs').as_uri(),'WRITER':(root/'src/writer.mjs').as_uri(),'DIR':str(Path(temp.name)/'engine'),'CLOCK':str(clock)}.items():
            code=code.replace(key,json.dumps(value))
        process=await asyncio.create_subprocess_exec('node','--input-type=module','--eval',code,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        async def stop():
            if process.returncode is None:
                process.terminate();await asyncio.wait_for(process.wait(),5)
        self.addAsyncCleanup(stop)
        port=int(await asyncio.wait_for(process.stdout.readline(),10))
        engine=AutumnEngine(url='http://127.0.0.1:{}'.format(port),token='scratch')
        for operation,payload in [('draw',{'seeds':['Alice']+['R{}'.format(i) for i in range(2,16)]+['Bob']}),('racetime',{'racetimeIds':{'Alice':'a','Bob':'b'}}),('series',{'bestOf':3}),('migrate',{'edition':'2026'})]:
            self.assertTrue((await engine._post(operation,payload)).ok)
        document=(await engine.state())['document']
        self.assertTrue((await engine._post('autonomy',dict(edition='2026',level='hold-all',applyPending=False,expectedRevision=document['revision'],policyRevision=document['autonomy']['revision'],actor='council',decisionId='hold'))).ok)
        opened=[];woken=[];notices=[];markers=[]
        async def opener(race,row):
            actions=(await engine.state())['document']['actions']
            self.assertTrue(any(a['status']=='claimed' for a in actions.values()))
            opened.append(race.match_id);markers.append(race.room_marker)
            return UNCERTAIN_RACE if race.identity.game == 1 else 'https://racetime.gg/z1r/game-{}'.format(race.identity.game)
        async def recover(race,action):
            self.assertEqual(race.room_marker,markers[0]);return ROOM
        async def wake(race,channel):woken.append(channel)
        async def announce(race,row,url):notices.append(url)
        source=FakeSource(HEADER+',Match,Game\n'+row(START,'Alice','Bob','z1rracing')+',W1-1,1')
        def scheduler():return AutumnScheduler(source,engine,Log(),open_room=opener,wake_booth=wake,announce=announce,recover_room=recover)
        first=scheduler();await first.tick(at(START,25))
        self.assertEqual(opened,[]);self.assertEqual(woken,[])
        actions=(await engine.state())['document']['actions'];action=next(a for a in actions.values() if a['kind']=='race-room')
        self.assertEqual(action['status'],'pending')
        self.assertTrue((await engine._post('decideRoom',dict(edition='2026',actionId=action['id'],actionRevision=action['revision'],decision='open',decisionId='yes',actor='council'))).ok)
        await first.tick(at(START,24));self.assertEqual(opened,['W1-1']);self.assertEqual(notices,[])
        await scheduler().tick(at(START,23));self.assertEqual(opened,['W1-1']);self.assertEqual(notices,[], 'v2 queues notices for Discord instead of posting here')
        saved=(await engine.state())['document'];self.assertEqual(saved['rooms']['W1-1|1']['room'],ROOM)
        self.assertEqual(len([a for a in saved['actions'].values() if a['kind']=='room-announcement']),1)
        await scheduler().tick(at(START,22))
        self.assertEqual(len([a for a in (await engine.state())['document']['actions'].values() if a['kind']=='room-announcement']),1)

        # Three explicit times; Game 1 completing cannot start Game 2 early.
        game2=START+timedelta(hours=3);game3=START+timedelta(days=1)
        source.csv_text=HEADER+',Match,Game\n'+'\n'.join(row(when,'Alice','Bob')+',W1-1,{}'.format(game) for game,when in [(3,game3),(1,START),(2,game2)])
        self.assertTrue((await engine._post('game',dict(edition='2026',matchId='W1-1',game=1,winner='Alice',room=ROOM))).ok)
        await scheduler().tick(at(START,20))
        self.assertEqual(opened,['W1-1'])
        state=(await engine.state())['document']
        self.assertEqual(state['times']['W1-1']['at'],START.isoformat())
        self.assertEqual(state['gameTimes']['W1-1']['2']['at'],game2.isoformat())
        game2+=timedelta(hours=1)
        source.csv_text=HEADER+',Match,Game\n'+'\n'.join(row(when,'Alice','Bob')+',W1-1,{}'.format(game) for game,when in [(2,game2),(3,game3),(1,START)])
        await scheduler().tick(at(START,19))
        state=(await engine.state())['document']
        self.assertEqual(state['gameTimes']['W1-1']['2']['at'],game2.isoformat())
        self.assertEqual(state['times']['W1-1']['at'],START.isoformat())
        # Switch to automatic result recording through the same saved policy.
        self.assertTrue((await engine._post('autonomy',dict(edition='2026',level='auto-run',applyPending=False,expectedRevision=state['revision'],policyRevision=state['autonomy']['revision'],actor='council',decisionId='auto'))).ok)
        finishing=[(2,game2,'a')] if two_zero else [(2,game2,'b'),(3,game3,'a')]
        for game,when,winner in finishing:
            clock.write_text(at(when,25).isoformat())
            await scheduler().tick(at(when,25))
            self.assertEqual(len(opened),game)
            room='https://racetime.gg/z1r/game-{}'.format(game)
            outcome=await engine.observe_result(dict(event='autumn',edition='2026',matchId='W1-1',game=game,room=room,observationId='finish-{}'.format(game),status='finished',entrants=[dict(id=who,status='done',finishSeconds='100' if who==winner else '101') for who in ('a','b')]))
            self.assertTrue(outcome.ok)
            await scheduler().tick(at(when,24))
            self.assertEqual(len(opened),game)
        state=(await engine.state())['document']
        self.assertEqual(state['results']['W1-1'],'Alice')
        self.assertEqual(len(state['games']['W1-1']),2 if two_zero else 3)
        self.assertEqual(len(state['rooms']),2 if two_zero else 3)
        self.assertEqual(state['times']['W1-1']['at'],START.isoformat())

        if two_zero:
            clock.write_text(at(game3,25).isoformat())
            await scheduler().tick(at(game3,25))
            self.assertEqual(len(opened),2, 'unused Game 3 never opens')
            source.csv_text=HEADER+',Match,Game\n'+'\n'.join(row(when,'Alice','Bob')+',W1-1,{}'.format(game) for game,when in [(1,START),(2,game2)])
            await scheduler().tick(at(game3,24))
            state=(await engine.state())['document']
            self.assertEqual(state['gameTimes']['W1-1']['3']['status'],'cancelled')
            self.assertEqual(state['results']['W1-1'],'Alice')

class RetiredRoomTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_retirement_clears_local_copies_and_never_reimports_them(self):
        from types import SimpleNamespace
        imports=[]
        class Engine:
            edition='2026'
            async def import_room(self,payload):imports.append(payload);return Written(RECORDED)
            async def room_work(self,payload):return Written(RECORDED,answer={'mayOpen':True,'action':{'id':'new'}})
        runner=AutumnScheduler(FakeSource(''),Engine(),Log())
        runner._workflow=True;runner._workflow_document={'actions':{'old':{'kind':'race-room','matchId':'W1-1','game':1,'room':ROOM,'replacementDecisionId':'council'}}}
        key=runner._match_key('W1-1',1);runner.created.update({key:ROOM,'autumn|W1-1':ROOM});runner.announced[key]=True;runner.booth_notices[key]=True
        race=SimpleNamespace(match_id='W1-1',identity=SimpleNamespace(game=1),at=START,runner_one='Alice',runner_two='Bob')
        self.assertTrue((await runner._room_work(race))['mayOpen']);self.assertEqual(imports,[])
        self.assertNotIn(key,runner.created);self.assertNotIn(key,runner.announced)
        await runner._room_work(race);self.assertEqual(imports,[],'old edition-less entry cannot revive the retired room')

    async def test_runner_does_not_delay_engine_lead_times_using_legacy_constants(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        runner=AutumnScheduler(FakeSource(''),SimpleNamespace(edition='2026'),Log())
        runner._workflow=True;runner._workflow_document={'settings':{'wakeLeadMinutes':50,'roomLeadMinutes':40}}
        runner._mirror=AsyncMock(return_value=True);runner._room_work=AsyncMock(return_value={'mayWake':False,'mayOpen':False});runner._room_v2=AsyncMock(return_value=None)
        race=SimpleNamespace(match_id='W1-1',identity=SimpleNamespace(game=1),at=START,status='scheduled',conditional=False)
        await runner._handle(race,None,at(START,45));runner._room_work.assert_awaited_once()

class AnnouncementChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_notice_carries_the_assigned_channel_and_crew(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        for channel in ('Z1Rracing2', ''):
            with self.subTest(channel=channel):
                engine=SimpleNamespace(edition='2026',queue_announcement=AsyncMock(return_value=Written(RECORDED)))
                runner=AutumnScheduler(FakeSource(''),engine,Log())
                runner._workflow=True
                race=SimpleNamespace(match_id='W1-1',identity=SimpleNamespace(game=1))
                scheduled=parse_schedule(sheet(row(START,'Alice','Bob',channel))).rows[0]
                await runner._tell(race,scheduled,ROOM)
                request=engine.queue_announcement.call_args.args[0]
                self.assertEqual(request['restreamChannel'],channel)
                self.assertEqual(request['crew'],['Bogie'] if channel else [])
                await runner._tell(race,scheduled,ROOM)
                engine.queue_announcement.assert_awaited_once()


class LateBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_room_still_wakes_when_broadcast_is_assigned_late(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        runner=AutumnScheduler(FakeSource(''),SimpleNamespace(edition='2026'),Log())
        runner._workflow=True;runner._workflow_document={}
        runner._mirror=AsyncMock(return_value=True)
        runner._room_work=AsyncMock(return_value={'mayWake':False,'room':{'room':ROOM}})
        runner._room_v2=AsyncMock(return_value=ROOM)
        runner._wake=AsyncMock();runner._let_in=AsyncMock();runner._tell=AsyncMock()
        race=SimpleNamespace(match_id='W1-1',identity=SimpleNamespace(game=1),at=START,status='scheduled',conditional=False)
        await runner._handle(race,SimpleNamespace(channel='Z1Rracing'),START+timedelta(minutes=10))
        runner._wake.assert_awaited_once()
