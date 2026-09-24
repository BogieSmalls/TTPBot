import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from ttpbot.config import TIMEZONE
from ttpbot.grace import (
    GRACE_CAP,
    GRACE_REGEN_CAP,
    GRACE_START,
    Entrant,
    GraceLedger,
    GraceRace,
    entrants_from,
)

START = datetime(2026, 9, 28, 20, 0, tzinfo=TIMEZONE)


def _ledger(balances=None, season='TTP5'):
    directory = Path(tempfile.mkdtemp())
    ledger = GraceLedger(directory / 'grace.json', season)
    ledger.balances.update(balances or {})
    return ledger


def _entrant(name, ready=True, moderator=False):
    return Entrant(user_id=name.lower(), name=name, ready=ready, moderator=moderator)


def _run(race, entrants, minutes, ledger):
    """Tick a race minute by minute, applying each decision as it lands."""
    seen = []
    for minute in minutes:
        decision = race.tick(START + timedelta(minutes=minute), entrants)
        ledger.apply(decision, entrants)
        seen.append(decision)
    return seen


class LedgerTests(unittest.TestCase):
    def test_unknown_racers_start_at_three_and_earning_caps_at_five(self):
        ledger = _ledger()
        self.assertEqual(ledger.balance('nobody'), GRACE_START)
        for _ in range(5):
            ledger.apply(type('D', (), {'spend': {}, 'earn': ['a']})())
        self.assertEqual(ledger.balance('a'), GRACE_CAP)

    def test_spending_never_goes_below_zero(self):
        ledger = _ledger({'a': 1})
        ledger.apply(type('D', (), {'spend': {'a': 4}, 'earn': []})())
        self.assertEqual(ledger.balance('a'), 0)

    def test_balances_persist_but_a_new_season_wipes_them(self):
        directory = Path(tempfile.mkdtemp())
        ledger = GraceLedger(directory / 'grace.json', 'TTP5')
        ledger.balances['a'] = 5
        ledger.save()
        self.assertEqual(GraceLedger(directory / 'grace.json', 'TTP5').balance('a'), 5)
        self.assertEqual(GraceLedger(directory / 'grace.json', 'TTP6').balance('a'), GRACE_START)

    def test_a_corrupt_ledger_starts_fresh_rather_than_raising(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / 'grace.json'
        path.write_text('{not json', encoding='utf-8')
        self.assertEqual(GraceLedger(path, 'TTP5').balance('a'), GRACE_START)


class EntrantParsingTests(unittest.TestCase):
    PAYLOAD = {
        'entrants': [
            {'user': {'id': '1', 'name': 'Ready', 'can_moderate': False}, 'status': {'value': 'ready'}},
            {'user': {'id': '2', 'name': 'Late', 'can_moderate': False}, 'status': {'value': 'not_ready'}},
            {'user': {'id': '3', 'name': 'Mod', 'can_moderate': True}, 'status': {'value': 'not_ready'}},
            {'user': {'id': '4', 'name': 'Invited', 'can_moderate': False}, 'status': {'value': 'invited'}},
        ],
    }

    def test_only_racers_in_the_room_count_and_moderators_are_flagged(self):
        entrants = entrants_from(self.PAYLOAD)
        self.assertEqual([e.name for e in entrants], ['Ready', 'Late', 'Mod'])
        self.assertEqual([e.ready for e in entrants], [True, False, False])
        self.assertEqual([e.moderator for e in entrants], [False, False, True])

    def test_room_monitors_and_the_opener_count_as_moderators(self):
        entrants = entrants_from(self.PAYLOAD, monitors=[{'id': '2'}], opened_by={'id': '1'})
        self.assertEqual([e.moderator for e in entrants], [True, True, True])


class CountdownTests(unittest.TestCase):
    def test_a_room_that_is_ready_on_time_earns_and_says_nothing(self):
        ledger = _ledger()
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('B')]
        decision = race.tick(START, entrants)
        ledger.apply(decision, entrants)
        self.assertEqual(decision.messages, [])
        self.assertFalse(decision.force_start)
        self.assertEqual(ledger.balance('a'), GRACE_START + 1)

    def test_a_straggler_spends_a_minute_a_minute_then_the_race_starts(self):
        ledger = _ledger({'late': 3})
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('B'), _entrant('Late', ready=False)]
        decisions = _run(race, entrants, [0, 1, 2, 3], ledger)

        self.assertIn('Not ready: Late (3 grace)', decisions[0].messages[0])
        self.assertEqual([d.spend for d in decisions], [{}, {'late': 1}, {'late': 1}, {'late': 1}])
        self.assertTrue(decisions[3].force_start)
        self.assertIn('removed from the race: Late', decisions[3].messages[-1])
        self.assertEqual(ledger.balance('late'), 0)
        # The racers who were on time still earned, once.
        self.assertEqual(ledger.balance('a'), GRACE_START + 1)

    def test_nobody_waits_longer_than_five_minutes(self):
        ledger = _ledger({'late': GRACE_CAP})
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('B'), _entrant('Late', ready=False)]
        decisions = _run(race, entrants, [0, 1, 2, 3, 4, 5], ledger)
        self.assertTrue(decisions[-1].force_start)
        self.assertEqual(ledger.balance('late'), 0)

    def test_a_racer_already_out_of_grace_starts_the_race_at_once(self):
        ledger = _ledger({'late': 0})
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('B'), _entrant('Late', ready=False)]
        decision = race.tick(START, entrants)
        self.assertTrue(decision.force_start)
        self.assertEqual(decision.spend, {})

    def test_a_latecomer_gets_a_minute_before_being_charged(self):
        ledger = _ledger({'late': 3})
        race = GraceRace(START, ledger, enforce=True)
        present = [_entrant('A'), _entrant('B')]
        race.tick(START - timedelta(minutes=5), present)
        arriving = present + [_entrant('Late', ready=False)]
        decisions = [race.tick(START + timedelta(minutes=m), arriving) for m in (1, 2, 3)]
        for decision in decisions:
            ledger.apply(decision, arriving)
        # Seen at +1, free until +2, first charged at +3.
        self.assertEqual([d.spend for d in decisions], [{}, {}, {'late': 1}])

    def test_an_unready_moderator_stops_the_bot_starting_anything(self):
        ledger = _ledger({'mod': 0})
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('B'), _entrant('Mod', ready=False, moderator=True)]
        decision = race.tick(START + timedelta(minutes=5), entrants)
        self.assertFalse(decision.force_start)
        self.assertEqual(decision.spend, {})
        self.assertIn('race monitor', decision.messages[-1])
        self.assertIn('moderator not ready', decision.blocked)

    def test_moderators_neither_earn_nor_are_charged(self):
        ledger = _ledger()
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('Mod', moderator=True)]
        decision = race.tick(START, entrants)
        ledger.apply(decision, entrants)
        self.assertEqual(decision.earn, ['a'])
        self.assertEqual(ledger.balance('mod'), GRACE_START)

    def test_never_leaves_fewer_than_two_racers(self):
        ledger = _ledger({'late': 0, 'other': 0})
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('Late', ready=False), _entrant('Other', ready=False)]
        decision = race.tick(START, entrants)
        self.assertFalse(decision.force_start)
        self.assertIn('fewer than two racers', decision.messages[-1])
        self.assertIn('only 1 ready', decision.blocked)

    def test_watch_only_mode_says_what_it_would_have_done_and_does_nothing(self):
        ledger = _ledger({'late': 0})
        race = GraceRace(START, ledger, enforce=False)
        entrants = [_entrant('A'), _entrant('B'), _entrant('Late', ready=False)]
        decision = race.tick(START, entrants)
        self.assertFalse(decision.force_start)
        self.assertIn('would have force started', decision.messages[-1])
        self.assertIn('Nobody is removed today', decision.messages[-1])

    def test_the_countdown_stops_once_everyone_is_ready(self):
        ledger = _ledger({'late': 3})
        race = GraceRace(START, ledger, enforce=True)
        waiting = [_entrant('A'), _entrant('Late', ready=False)]
        race.tick(START + timedelta(minutes=1), waiting)
        ready = [_entrant('A'), _entrant('Late')]
        decision = race.tick(START + timedelta(minutes=2), ready)
        self.assertFalse(decision.force_start)
        self.assertEqual(decision.spend, {})
        # And a later tick after the deadline still does nothing.
        self.assertFalse(race.tick(START + timedelta(minutes=9), ready).force_start)

    def test_nothing_happens_before_the_scheduled_time(self):
        ledger = _ledger({'late': 0})
        race = GraceRace(START, ledger, enforce=True)
        entrants = [_entrant('A'), _entrant('B'), _entrant('Late', ready=False)]
        decision = race.tick(START - timedelta(seconds=30), entrants)
        self.assertFalse(decision.force_start)
        self.assertEqual(decision.messages, [])


if __name__ == '__main__':
    unittest.main()


class IdleAccrualTests(unittest.TestCase):
    """Grace regenerates a minute a day, so a bad night is not permanent.

    Someone who spends their balance and then races again a week later has
    earned it back in between, which is the point: the ledger punishes repeat
    lateness, not one bad night months ago.
    """

    def test_an_idle_day_grants_one_minute(self):
        ledger = _ledger({'a': 2})

        ledger.accrue(START)
        self.assertEqual(ledger.balance('a'), 2)

        ledger.accrue(START + timedelta(days=1))
        self.assertEqual(ledger.balance('a'), 3)

    def test_accrual_is_idempotent_within_a_day(self):
        ledger = _ledger({'a': 2})
        ledger.accrue(START)

        # START is 20:00, so these stay inside the same calendar day.
        for hour in (1, 2, 3):
            ledger.accrue(START + timedelta(hours=hour))

        self.assertEqual(ledger.balance('a'), 2)

    def test_a_gap_grants_a_minute_per_day_up_to_the_starting_balance(self):
        ledger = _ledger({'spent': 0, 'partial': 2})
        ledger.accrue(START)

        ledger.accrue(START + timedelta(days=3))

        self.assertEqual(ledger.balance('spent'), GRACE_REGEN_CAP)
        self.assertEqual(ledger.balance('partial'), GRACE_REGEN_CAP)

    def test_idle_days_never_carry_past_the_starting_balance(self):
        # Above the starting balance is earned by being on time, never by
        # waiting: idling for a season still leaves you at the baseline.
        ledger = _ledger({'a': 1})
        ledger.accrue(START)

        ledger.accrue(START + timedelta(days=90))

        self.assertEqual(ledger.balance('a'), GRACE_REGEN_CAP)
        self.assertLess(GRACE_REGEN_CAP, GRACE_CAP)

    def test_earned_minutes_above_the_baseline_are_left_alone(self):
        ledger = _ledger({'earner': 5, 'above': 4})
        ledger.accrue(START)

        ledger.accrue(START + timedelta(days=30))

        self.assertEqual(ledger.balance('earner'), 5)
        self.assertEqual(ledger.balance('above'), 4)

    def test_accrual_does_not_invent_balances_for_unseen_racers(self):
        ledger = _ledger({'a': 1})
        ledger.accrue(START)
        ledger.accrue(START + timedelta(days=2))

        self.assertNotIn('stranger', ledger.balances)
        self.assertEqual(ledger.balance('stranger'), GRACE_START)

    def test_spending_today_still_accrues_exactly_one_tomorrow(self):
        ledger = _ledger()
        ledger.accrue(START)
        ledger.apply(type('D', (), {'spend': {'a': 2}, 'earn': []})())
        self.assertEqual(ledger.balance('a'), 1)

        ledger.accrue(START + timedelta(days=1))

        self.assertEqual(ledger.balance('a'), 2)

    def test_accrual_survives_a_restart(self):
        directory = Path(tempfile.mkdtemp())
        ledger = GraceLedger(directory / 'grace.json', 'TTP5')
        ledger.balances['a'] = 1
        ledger.accrue(START)
        ledger.save()

        reloaded = GraceLedger(directory / 'grace.json', 'TTP5')
        reloaded.accrue(START + timedelta(days=1))

        self.assertEqual(reloaded.balance('a'), 2)

    def test_a_race_accrues_before_it_reports_balances(self):
        ledger = _ledger({'late': 0})
        ledger.accrue(START - timedelta(days=2))

        race = GraceRace(START, ledger, enforce=True)
        race.tick(START - timedelta(minutes=5), [_entrant('Late', ready=False)])

        self.assertEqual(ledger.balance('late'), 2)
