"""The tournament's own state: match identities, and losing them safely.

Two things the store did not do before, and both had to be added rather than
worked around.

A tournament key names a *match*, not a moment. The League's keys start with a
timestamp, and `cleanup_before` prunes on it -- which is right for a fixture that
has been and gone, and wrong for a binding. A match scheduled for tonight,
postponed twice and raced next week is one binding the whole way through, and
forgetting it is how a grand-final row gets reassigned to the reset.

And a quarantined state file used to become invisible. The read that found the
corruption raised, but it moved the file aside, so every read after a restart
said "no state yet, carry on". For the tournament that is the worst answer
available: forgotten bindings reassign a finals row, and a forgotten room is a
second room.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ttpbot.state import DestinationStateStore, StateStoreError

DESTINATION = 'https://racetime.gg|z1r'
ROW = 'autumn|bogie-vs-merks|2026-10-02T22:00:00-04:00'
ROOM = 'https://racetime.gg/z1r/fancy-mario-1234'


class AutumnStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def store(self, kind, name=None):
        return DestinationStateStore(
            name or '{}.json'.format(kind), DESTINATION, kind, data_dir=self.root)


class Keys(AutumnStateTests):
    def test_a_binding_key_names_two_racers_and_a_time(self):
        it = self.store('autumn_bindings')
        it.save({ROW: 'W1-1'})
        self.assertEqual(it.load(), {ROW: 'W1-1'})

    def test_every_key_is_scoped_to_its_competition(self):
        # One relay can hold two tournaments, and neither may read the other's
        # bindings.
        it = self.store('autumn_bindings')
        with self.assertRaises(StateStoreError) as caught:
            it.save({'bogie-vs-merks|2026-10-02T22:00:00-04:00': 'W1-1'})
        # And the complaint says what is actually wrong. Read loosely, that key
        # parses as a competition called `bogie-vs-merks` with a timestamp for a
        # pair, and being told it "must name two racers" sends whoever reads the
        # log looking at the wrong half.
        self.assertIn('<competition>|<racers>|<time>', str(caught.exception))

        with self.assertRaises(StateStoreError) as wrong_competition:
            it.save({'Autumn!|bogie-vs-merks|2026-10-02T22:00:00-04:00': 'W1-1'})
        self.assertIn('name a competition', str(wrong_competition.exception))

    def test_one_competition_cannot_read_another(self):
        it = self.store('autumn_bindings')
        it.save({
            ROW: 'W1-1',
            'corto|bogie-vs-merks|2026-10-02T22:00:00-04:00': 'W2-1',
        })
        # Same pair, same time, different tournament: two entries, not one.
        self.assertEqual(len(it.load()), 2)

    def test_a_room_key_names_a_match(self):
        # And *only* a match. Not a time, which is the whole point: a postponed
        # race keeps its room because its identity never moved.
        it = self.store('autumn_created_races')
        it.save({'autumn|W1-1': ROOM, 'autumn|GF-2': ROOM})
        self.assertEqual(sorted(it.load()), ['autumn|GF-2', 'autumn|W1-1'])

    def test_a_key_that_is_not_a_match_is_refused(self):
        it = self.store('autumn_created_races')
        for key in ('autumn|W1', 'autumn|GF-3', 'autumn|whatever',
                    'autumn|2026-10-02T22:00:00-04:00', 'autumn|'):
            with self.subTest(key=key), self.assertRaises(StateStoreError):
                it.save({key: ROOM})

    def test_a_binding_key_needs_its_timestamp(self):
        it = self.store('autumn_bindings')
        for key in ('autumn|bogie-vs-merks', 'autumn|bogie-vs-merks|whenever',
                    'autumn|bogie-vs-merks|2026-10-02T22:00:00'):
            with self.subTest(key=key), self.assertRaises(StateStoreError):
                it.save({key: 'W1-1'})


class Values(AutumnStateTests):
    def test_a_binding_points_at_a_match_or_nothing(self):
        it = self.store('autumn_bindings')
        for value in ('nonsense', True, 42, '', 'W1'):
            with self.subTest(value=value), self.assertRaises(StateStoreError):
                it.save({ROW: value})

    def test_a_mirrored_time_is_an_instant_with_a_zone(self):
        it = self.store('autumn_mirrored_times')
        it.save({'autumn|W1-1': '2026-10-02T22:00:00-04:00'})
        self.assertEqual(it.load()['autumn|W1-1'], '2026-10-02T22:00:00-04:00')
        for value in ('2026-10-02T22:00:00', 'soon', True):
            with self.subTest(value=value), self.assertRaises(StateStoreError):
                it.save({'autumn|W1-1': value})

    def test_a_room_is_still_a_room(self):
        # Rooms reuse the created-race validation, so an Autumn room is checked
        # against the destination exactly as a League one is.
        it = self.store('autumn_created_races')
        it.save({'autumn|W1-1': ROOM})
        with self.assertRaises(StateStoreError):
            it.save({'autumn|W1-1': 'https://example.com/not-racetime'})

    def test_an_announcement_is_a_flag(self):
        it = self.store('autumn_sent_webhooks')
        it.save({'autumn|W1-1': True})
        self.assertEqual(it.load(), {'autumn|W1-1': True})
        with self.assertRaises(StateStoreError):
            it.save({'autumn|W1-1': 'yes'})


class Pruning(AutumnStateTests):
    def test_a_binding_is_never_pruned_by_time(self):
        # There is no cutoff after which a binding stops being true. Dropping one
        # because it is old is exactly the bug this kind exists to avoid.
        it = self.store('autumn_bindings')
        it.save({ROW: 'W1-1'})
        kept = it.cleanup_before(datetime.now(timezone.utc) + timedelta(days=365))
        self.assertEqual(kept, {ROW: 'W1-1'})
        self.assertEqual(it.load(), {ROW: 'W1-1'})

    def test_nor_is_a_room_or_a_mirrored_time(self):
        # A match postponed past the League's two-hour retention still has the
        # same room. That association must outlive the original start.
        future = datetime.now(timezone.utc) + timedelta(days=365)
        rooms = self.store('autumn_created_races')
        rooms.save({'autumn|W1-1': ROOM})
        self.assertEqual(rooms.cleanup_before(future), {'autumn|W1-1': ROOM})

        times = self.store('autumn_mirrored_times')
        times.save({'autumn|W1-1': '2026-10-02T22:00:00-04:00'})
        self.assertEqual(len(times.cleanup_before(future)), 1)

    def test_the_league_still_prunes(self):
        # Unchanged, and this is the test that says so.
        it = self.store('league_created_races', 'league.json')
        old = '2020-01-01T20:00:00+00:00|bogie-vs-merks'
        it.save({old: ROOM})
        self.assertEqual(it.cleanup_before(datetime.now(timezone.utc)), {})

    def test_prunes_by_time_says_which_is_which(self):
        self.assertFalse(self.store('autumn_bindings').prunes_by_time)
        self.assertTrue(self.store('league_created_races', 'l.json').prunes_by_time)


class LosingIt(AutumnStateTests):
    def corrupt(self, kind, name):
        (self.root / name).write_text('{not json', encoding='utf-8')
        return lambda: self.store(kind, name)

    def test_a_quarantine_survives_a_restart(self):
        # The bug: the first read raised, the file was moved aside, and every read
        # after that returned {} -- so a restart turned "we lost this" into "we
        # never had it".
        again = self.corrupt('autumn_bindings', 'autumn_bindings.json')

        with self.assertRaises(StateStoreError) as first:
            again().load()
        self.assertIn('quarantined', str(first.exception))

        with self.assertRaises(StateStoreError) as restarted:
            again().load()
        self.assertIn('not recovered', str(restarted.exception))

    def test_it_refuses_to_write_over_the_hole(self):
        # Saving partial state on top of a hole hides the thing somebody needs to
        # look at, and the partial state then looks authoritative.
        again = self.corrupt('autumn_bindings', 'autumn_bindings.json')
        with self.assertRaises(StateStoreError):
            again().load()
        with self.assertRaises(StateStoreError) as caught:
            again().save({ROW: 'W1-1'})
        self.assertIn('refusing to write', str(caught.exception))

    def test_removing_the_marker_is_the_recovery(self):
        again = self.corrupt('autumn_bindings', 'autumn_bindings.json')
        with self.assertRaises(StateStoreError):
            again().load()

        marker = self.root / 'autumn_bindings.json.unrecovered'
        self.assertTrue(marker.exists())
        # It says what happened and what removing it means.
        note = marker.read_text(encoding='utf-8')
        self.assertIn('quarantined', note)
        self.assertIn('until this file is removed', note)

        marker.unlink()
        self.assertEqual(again().load(), {})

    def test_the_corrupt_bytes_are_kept(self):
        # Recovery needs something to recover *from*.
        again = self.corrupt('autumn_bindings', 'autumn_bindings.json')
        with self.assertRaises(StateStoreError):
            again().load()
        kept = [p for p in self.root.iterdir() if '.corrupt-' in p.name]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(encoding='utf-8'), '{not json')

    def test_the_league_keeps_its_current_forgiving_behavior(self):
        # Deliberately unchanged. The League has behaved this way in production
        # for months, and changing it is its own decision rather than a side
        # effect of the tournament's. Noted as a known gap, not a fix.
        again = self.corrupt('league_created_races', 'league.json')
        with self.assertRaises(StateStoreError):
            again().load()
        self.assertEqual(again().load(), {})


if __name__ == '__main__':
    unittest.main()
