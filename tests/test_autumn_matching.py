"""Which match a schedule row is about.

The Schedule tab names two racers and a time, and never a match. These are the
ways that is ambiguous and what each one should do about it.
"""

import unittest
from datetime import datetime, timedelta, timezone

from ttpbot.autumn.matching import (
    RaceIdentity,
    match_rows,
    strip_prefix,
)

ET = timezone(timedelta(hours=-4))
TONIGHT = datetime(2026, 10, 6, 22, 0, tzinfo=ET)


def row(one, two, at=TONIGHT):
    return {'at': at, 'runner_one': one, 'runner_two': two}


def bracket(**overrides):
    """A small bracket, with the finals reachable."""
    matches = {
        'W1-1': {'a': 'ISUMatt', 'b': 'chessjerk', 'state': 'ready'},
        'W1-2': {'a': 'Iceblue', 'b': 'equations19', 'state': 'ready'},
        'L1-1': {'a': None, 'b': None, 'state': 'waiting'},
        'GF-1': {'a': 'Bogie', 'b': 'shatty', 'state': 'ready'},
        # Before the final, the engine calls the reset `not-needed` -- the same
        # word it uses once the final has ruled it out. Only GF-1's state
        # separates "not yet" from "not ever".
        'GF-2': {'a': None, 'b': None, 'state': 'not-needed'},
    }
    matches.update(overrides)
    return matches


class PrefixTests(unittest.TestCase):
    def test_the_ranking_prefix_comes_off(self):
        # The form's dropdowns are rank-prefixed, so that is what lands on the
        # sheet. The League strips a team abbreviation the same way.
        self.assertEqual(strip_prefix('(5) Bogie'), 'Bogie')
        self.assertEqual(strip_prefix('(46) ISUMatt'), 'ISUMatt')
        self.assertEqual(strip_prefix('Bogie'), 'Bogie')
        self.assertEqual(strip_prefix('  (1) ta909  '), 'ta909')
        self.assertEqual(strip_prefix(None), '')


class OrdinaryRowTests(unittest.TestCase):
    def test_a_row_finds_its_match(self):
        found = match_rows([row('(46) ISUMatt', '(7) chessjerk')], bracket())
        self.assertEqual(found.unresolved, [])
        self.assertEqual(len(found.resolved), 1)
        race = found.resolved[0]
        self.assertEqual(race.match_id, 'W1-1')
        self.assertEqual(race.identity, RaceIdentity('autumn', 'W1-1', 1))
        self.assertFalse(race.conditional)

    def test_the_identity_is_the_match_and_not_the_pair_or_the_time(self):
        # Keyed by the one thing that does not change. Keying by the start is how
        # a postponed race gets a second room.
        found = match_rows([row('ISUMatt', 'chessjerk')], bracket())
        self.assertEqual(found.resolved[0].identity.key, 'autumn|W1-1|1')

        later = match_rows(
            [row('ISUMatt', 'chessjerk', at=TONIGHT + timedelta(days=1))], bracket())
        self.assertEqual(later.resolved[0].identity, found.resolved[0].identity)

    def test_names_are_matched_flattened(self):
        # eatmysteel against Eatmysteel has already cost a racer their ranking on
        # the page; it must not also cost them their room.
        found = match_rows([row('isumatt', 'CHESSJERK')], bracket())
        self.assertEqual(found.resolved[0].match_id, 'W1-1')

    def test_a_pair_nobody_is_racing_is_reported(self):
        found = match_rows([row('Bogie', 'ISUMatt')], bracket())
        self.assertEqual(found.resolved, [])
        self.assertEqual(len(found.unresolved), 1)
        self.assertIn('no match in the bracket', found.unresolved[0].reason)

    def test_a_match_already_played_is_reported_rather_than_reopened(self):
        played = bracket(**{'W1-1': {'a': 'ISUMatt', 'b': 'chessjerk', 'state': 'played'}})
        found = match_rows([row('ISUMatt', 'chessjerk')], played)
        self.assertEqual(found.resolved, [])
        self.assertIn('already been played', found.unresolved[0].reason)

    def test_one_person_against_themselves_is_reported(self):
        found = match_rows([row('Bogie', '(5) Bogie')], bracket())
        self.assertEqual(found.resolved, [])
        self.assertIn('same person', found.unresolved[0].reason)

    def test_a_match_not_raceable_yet_is_scheduled_but_conditional(self):
        waiting = bracket(**{'L1-1': {'a': 'ISUMatt', 'b': 'Iceblue', 'state': 'waiting'}})
        found = match_rows([row('ISUMatt', 'Iceblue')], waiting)
        self.assertEqual(found.resolved[0].match_id, 'L1-1')
        self.assertTrue(found.resolved[0].conditional)
        self.assertEqual(found.raceable(), [])


class FinalsTests(unittest.TestCase):
    """GF-1 and GF-2 are the same two people, so the pair cannot separate them."""

    def test_two_rows_are_the_final_and_then_the_reset(self):
        found = match_rows([
            row('shatty', 'Bogie', at=TONIGHT + timedelta(hours=1)),
            row('Bogie', 'shatty', at=TONIGHT),
        ], bracket())
        self.assertEqual([race.match_id for race in found.resolved], ['GF-1', 'GF-2'])
        # Time order decides, not the order the rows happen to be in.
        self.assertEqual(found.resolved[0].at, TONIGHT)
        self.assertEqual(found.resolved[1].at, TONIGHT + timedelta(hours=1))

    def test_the_reset_is_schedulable_before_it_is_raceable(self):
        # Finalists agree both times at once; nobody wants to arrange a second
        # race at one in the morning. So the reset is resolved and held.
        found = match_rows([
            row('Bogie', 'shatty', at=TONIGHT),
            row('Bogie', 'shatty', at=TONIGHT + timedelta(hours=1)),
        ], bracket())
        reset = found.resolved[1]
        self.assertEqual(reset.match_id, 'GF-2')
        self.assertTrue(reset.conditional)
        self.assertIn('only if the final', reset.why_conditional)
        self.assertEqual([race.match_id for race in found.raceable()], ['GF-1'])

    def test_one_row_before_the_final_is_the_final(self):
        found = match_rows([row('Bogie', 'shatty')], bracket())
        self.assertEqual(found.resolved[0].match_id, 'GF-1')
        self.assertFalse(found.resolved[0].conditional)

    def test_one_row_after_a_final_that_needs_a_reset_is_the_reset(self):
        after = bracket(
            **{
                'GF-1': {'a': 'Bogie', 'b': 'shatty', 'state': 'played'},
                'GF-2': {'a': 'shatty', 'b': 'Bogie', 'state': 'ready'},
            }
        )
        found = match_rows([row('Bogie', 'shatty')], after)
        self.assertEqual(found.resolved[0].match_id, 'GF-2')
        self.assertFalse(found.resolved[0].conditional)

    def test_a_final_won_without_a_reset_leaves_nothing_to_race(self):
        # The engine says `not-needed`, and that answer is taken rather than
        # recomputed -- two copies of the reset rule would eventually disagree
        # about who won a tournament.
        done = bracket(
            **{
                'GF-1': {'a': 'Bogie', 'b': 'shatty', 'state': 'played'},
                'GF-2': {'a': None, 'b': None, 'state': 'not-needed'},
            }
        )
        found = match_rows([row('Bogie', 'shatty')], done)
        self.assertEqual(found.resolved, [])
        self.assertEqual(found.unresolved, [])

    def test_the_reset_becomes_raceable_once_the_final_calls_for_it(self):
        rows = [
            row('Bogie', 'shatty', at=TONIGHT),
            row('Bogie', 'shatty', at=TONIGHT + timedelta(hours=1)),
        ]
        before = match_rows(rows, bracket())
        self.assertTrue(before.resolved[1].conditional)

        after = match_rows(rows, bracket(
            **{
                'GF-1': {'a': 'Bogie', 'b': 'shatty', 'state': 'played'},
                'GF-2': {'a': 'shatty', 'b': 'Bogie', 'state': 'ready'},
            }
        ))
        reset = [race for race in after.resolved if race.match_id == 'GF-2'][0]
        self.assertFalse(reset.conditional)
        self.assertIn('GF-2', [race.match_id for race in after.raceable()])

    def test_the_finals_rows_keep_their_own_identities(self):
        found = match_rows([
            row('Bogie', 'shatty', at=TONIGHT),
            row('Bogie', 'shatty', at=TONIGHT + timedelta(hours=1)),
        ], bracket())
        self.assertEqual(
            [race.identity.key for race in found.resolved],
            ['autumn|GF-1|1', 'autumn|GF-2|1'],
        )


if __name__ == '__main__':
    unittest.main()


class BindingTests(unittest.TestCase):
    """A row keeps the match it was first given.

    The bracket moves under these rows: matches get played and racers advance.
    A row whose answer changes because of that is a room opened at a time nobody
    agreed to.
    """

    def test_a_finished_reset_is_not_raceable(self):
        # "Not ruled out" is not the same claim as "ready". A finished reset is
        # not `not-needed`, so a check for that alone called it raceable -- and
        # would have opened a room for a tournament that was already over.
        over = bracket(**{
            'GF-1': {'a': 'Bogie', 'b': 'shatty', 'state': 'played'},
            'GF-2': {'a': 'shatty', 'b': 'Bogie', 'state': 'played'},
        })
        found = match_rows([row('Bogie', 'shatty')], over)
        self.assertEqual(found.raceable(), [])

    def test_a_finished_ordinary_match_is_not_raceable_either(self):
        played = bracket(**{'W1-1': {'a': 'ISUMatt', 'b': 'chessjerk', 'state': 'played'}})
        found = match_rows([row('ISUMatt', 'chessjerk')], played)
        self.assertEqual(found.raceable(), [])

    def test_the_finals_row_stays_the_finals_row_after_the_final(self):
        """The gap that needed the binding.

        One row plus a finished final is, on its own, indistinguishable from a
        reset that somebody scheduled -- so the resolver drew the only conclusion
        available to it and handed GF-1's own time to GF-2.
        """
        before = match_rows([row('Bogie', 'shatty')], bracket())
        self.assertEqual(before.resolved[0].match_id, 'GF-1')

        after = match_rows(
            [row('Bogie', 'shatty')],
            bracket(**{
                'GF-1': {'a': 'Bogie', 'b': 'shatty', 'state': 'played'},
                'GF-2': {'a': 'shatty', 'b': 'Bogie', 'state': 'ready'},
            }),
            bindings=before.bindings,
        )
        # Nothing: that row's race has been run.
        self.assertEqual(after.resolved, [])

    def test_a_new_row_is_what_supplies_the_reset_its_time(self):
        first = match_rows([row('Bogie', 'shatty')], bracket())

        rows = [row('Bogie', 'shatty'), row('Bogie', 'shatty', at=TONIGHT + timedelta(hours=1))]
        after = match_rows(
            rows,
            bracket(**{
                'GF-1': {'a': 'Bogie', 'b': 'shatty', 'state': 'played'},
                'GF-2': {'a': 'shatty', 'b': 'Bogie', 'state': 'ready'},
            }),
            bindings=first.bindings,
        )
        self.assertEqual([race.match_id for race in after.resolved], ['GF-1', 'GF-2'])
        raceable = after.raceable()
        self.assertEqual([race.match_id for race in raceable], ['GF-2'])
        self.assertEqual(raceable[0].at, TONIGHT + timedelta(hours=1))

    def test_a_binding_survives_a_restart(self):
        # The store is the only memory, so a restart is a reload -- and a
        # forgotten binding is the bug above, arriving later.
        before = match_rows([row('ISUMatt', 'chessjerk')], bracket())
        as_persisted = dict(before.bindings)

        after = match_rows(
            [row('ISUMatt', 'chessjerk')], bracket(), bindings=as_persisted)
        self.assertEqual(after.resolved[0].match_id, 'W1-1')
        self.assertEqual(after.bindings, as_persisted)

    def test_a_rescheduled_row_is_a_new_row_and_finds_its_match_again(self):
        before = match_rows([row('ISUMatt', 'chessjerk')], bracket())
        moved = match_rows(
            [row('ISUMatt', 'chessjerk', at=TONIGHT + timedelta(days=1))],
            bracket(),
            bindings=before.bindings,
        )
        # Same match, new time. The identity is the match, so nothing downstream
        # treats this as a different race.
        self.assertEqual(moved.resolved[0].match_id, 'W1-1')
        self.assertEqual(moved.resolved[0].identity.key, 'autumn|W1-1|1')
