"""The pure half of picks in a TTP tournament room: what it says and records.

The copy is the spec's three mock rooms under "What racers see", with the real
names, round, game and score swapped in.
"""

import unittest

from ttpbot import tournament_room as tr

UPHILL = '143oNtDD4Pw3Yw6SRryTCXlqTsRz2fxg4s4pJW'
MUFFLE = '2487lt0moGMFLRug1ofQdxPuUBWnJVSsi7d202I'
PICK5 = 'p8GIOeYEAonHy5131VdG6b94Ts3EMMvBaQoWR'
FLAGSETS = [
    {'name': 'TTP5 Uphill Battle', 'flags': UPHILL, 'command': 'ttp5uphill'},
    {'name': 'TTP5 Muffle Rug', 'flags': MUFFLE, 'command': 'ttp5muffle'},
    {'name': 'TTP5 Pick 5', 'flags': PICK5, 'command': 'ttp5pick5'},
]
CONTEXT = dict(flagsets=FLAGSETS, tag='TTP5', round_label='Round 1')


def state(decision=None, games=None, chosen=None, warnings=()):
    """A pickState as the engine returns it, for thomjay (seed 25) vs specialk3782 (seed 40)."""
    better, other = 'thomjay', 'specialk3782'
    owners = {'1': None, '2': None}
    if decision == 'pick':
        owners = {'1': better, '2': other}
    elif decision == 'defer':
        owners = {'1': other, '2': better}
    games = {str(n): {'flagset': name, 'flags': '', 'source': 'room-start', 'room': 'r{}'.format(n)}
             for n, name in (games or {}).items()}
    played = [games[n]['flagset'] for n in sorted(games)]
    return {
        'matchId': 'W1-25', 'betterSeed': better, 'otherSeed': other,
        'decision': {'choice': decision, 'by': 'racer', 'actor': better} if decision else None,
        'owners': owners,
        'chosen': {str(n): {'flagset': name} for n, name in (chosen or {}).items()},
        'games': games, 'played': played,
        'left': [f['name'] for f in FLAGSETS if f['name'] not in played],
        'game3': None, 'warnings': list(warnings),
    }


def bot(text, name='SahasrahBot'):
    return {'is_bot': True, 'bot': name, 'message_plain': text}


def person(text, name='thomjay'):
    return {'is_bot': False, 'user': {'name': name}, 'message_plain': text, 'message': text}


ROLLED = 'Seed rolling complete.  See race info for details.'
RESET = 'Reseting bot state.  You may now roll a new game.'
CLEARED = 'Seed cleared. You may now roll a new one.'


class Rolls(unittest.TestCase):
    def test_two_flags_in_info_uses_the_last(self):
        info = 'TTP Season 5 Tournament | Flags: {} Seed: 1 - Flags: {} - Hash: A B C D'.format(UPHILL, PICK5)
        self.assertEqual(tr.current_roll(info), PICK5)
        self.assertEqual(tr.current_roll('Seed: 1 - Flags: {}'.format(MUFFLE)), MUFFLE)
        self.assertEqual(tr.current_roll('consternation - Flags: {} Seed: 9'.format(UPHILL)), UPHILL)
        self.assertIsNone(tr.current_roll(''))
        self.assertIsNone(tr.current_roll(None))

    def test_the_last_bot_word_wins(self):
        self.assertEqual(tr.roll_state([]), 'none')
        self.assertEqual(tr.roll_state([bot(ROLLED)]), 'rolled')
        self.assertEqual(tr.roll_state([bot(ROLLED), bot(RESET)]), 'cancelled')
        self.assertEqual(tr.roll_state([bot(ROLLED), bot(CLEARED, 'TTPBot')]), 'cancelled')
        self.assertEqual(tr.roll_state([bot(ROLLED), bot(RESET), bot(ROLLED)]), 'rolled')

    def test_a_person_typing_seed_cleared_changes_nothing(self):
        self.assertEqual(tr.roll_state([bot(ROLLED), person(CLEARED), person(RESET)]), 'rolled')
        self.assertEqual(tr.roll_state([person(ROLLED)]), 'none')

    def test_history_with_no_roll_records_nothing_rather_than_guess(self):
        state = tr.roll_state([bot('Hi!  I\'m SahasrahBot'), person('!flags ' + PICK5)])
        self.assertEqual(state, 'none')
        self.assertIsNone(tr.flags_at_start('Seed: 1 - Flags: {}'.format(PICK5), state))

    def test_flags_at_start_needs_a_roll_standing(self):
        info = 'Seed: 1 - Flags: {}'.format(PICK5)
        self.assertEqual(tr.flags_at_start(info, 'rolled'), PICK5)
        self.assertIsNone(tr.flags_at_start(info, 'cancelled'))
        self.assertIsNone(tr.flags_at_start('', 'rolled'))

    def test_the_log_counts_only_the_bots_lines(self):
        log = '\n'.join([
            '[2026-10-27 01:43:05+00:00] SahasrahBot: Hi!',
            '[2026-10-27 01:57:52+00:00] SahasrahBot updated the race information.',
            '[2026-10-27 01:57:52+00:00] SahasrahBot: Seed: 4 - Flags: ' + PICK5,
            '[2026-10-27 01:57:52+00:00] SahasrahBot: ' + ROLLED,
        ])
        self.assertEqual(tr.roll_state_from_log(log), 'rolled')
        cancelled = log + '\n[2026-10-27 01:58:04+00:00] SahasrahBot: ' + RESET
        self.assertEqual(tr.roll_state_from_log(cancelled), 'cancelled')
        self.assertEqual(tr.roll_state_from_log(log + '\n[t] TTPBot: ' + CLEARED), 'cancelled')
        # People carry a discriminator, so a person named SahasrahBot is not the bot.
        self.assertEqual(tr.roll_state_from_log(log + '\n[t] SahasrahBot#1234: ' + RESET), 'rolled')
        self.assertEqual(tr.roll_state_from_log(log + '\n[t] shatty#8786: ' + CLEARED), 'rolled')
        self.assertEqual(tr.roll_state_from_log(''), 'none')


class Opening(unittest.TestCase):
    def test_game_1_asks_the_better_seed(self):
        plan = tr.plan_for_room(state(), 1, **CONTEXT)
        self.assertEqual(plan.opening, [
            'TTP5 Round 1, Game 1: thomjay vs specialk3782.',
            "thomjay, you're the better seed, so this decision is yours: !pick to choose this game's "
            'flagset, or !defer to give it to specialk3782 and take Game 2.',
        ])
        self.assertEqual(plan.owner, 'thomjay')
        self.assertTrue(plan.needs_decision)
        self.assertEqual(plan.notices, [])

    def test_game_2_names_the_owner_and_what_was_played(self):
        plan = tr.plan_for_room(state('defer', games={1: 'TTP5 Uphill Battle'}), 2,
                                score={'thomjay': 1, 'specialk3782': 0}, **CONTEXT)
        self.assertEqual(plan.opening, [
            'TTP5 Round 1, Game 2: thomjay leads 1–0.',
            "thomjay owns this pick. Uphill Battle has been played. When you're ready: !ttp5muffle !ttp5pick5",
        ])
        self.assertEqual(plan.owner, 'thomjay')
        self.assertEqual(plan.commands, ['ttp5muffle', 'ttp5pick5'])

    def test_game_3_needs_no_pick_and_names_the_flagset_left(self):
        plan = tr.plan_for_room(state('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}), 3,
                                score={'thomjay': 1, 'specialk3782': 1}, **CONTEXT)
        self.assertEqual(plan.opening, [
            "TTP5 Round 1, Game 3: tied 1–1. This is Pick 5, the flagset left, so nobody picks. "
            "Roll it when everyone's ready: !flags " + PICK5,
        ])
        self.assertIsNone(plan.owner)
        self.assertFalse(plan.needs_decision)

    def test_a_roll_with_no_decision_and_game_2_names_nobody(self):
        plan = tr.plan_for_room(state(games={1: 'TTP5 Uphill Battle'}), 2,
                                score={'thomjay': 0, 'specialk3782': 1}, **CONTEXT)
        self.assertEqual(plan.opening, ['TTP5 Round 1, Game 2: specialk3782 leads 1–0.'])
        self.assertIsNone(plan.owner)
        self.assertEqual(plan.notices,
                         ['Nobody has recorded who picks Game 2, so this room names no picker until Council does'])
        self.assertNotIn('thomjay owns', ' '.join(plan.opening))

    def test_game_2_names_no_played_flagset_until_one_is_on_record(self):  # Review Focus 1
        plan = tr.plan_for_room(state('defer'), 2, **CONTEXT)
        self.assertEqual(plan.opening[1],
                         "thomjay owns this pick. When you're ready: !ttp5uphill !ttp5muffle !ttp5pick5")
        self.assertIn("Game 1's flagset isn't on record, so this room can't rule one out", plan.notices)

    def test_a_score_not_yet_confirmed_names_the_racers(self):
        plan = tr.plan_for_room(state('defer', games={1: 'TTP5 Uphill Battle'}), 2,
                                score={'thomjay': 0, 'specialk3782': 0}, **CONTEXT)
        self.assertEqual(plan.opening[0], 'TTP5 Round 1, Game 2: thomjay vs specialk3782.')

    def test_game_3_with_a_flagset_played_twice_goes_to_council(self):
        plan = tr.plan_for_room(state('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Uphill Battle'},
                                      warnings=['A flagset was played twice, so Council decides Game 3.']),
                                3, **CONTEXT)
        self.assertEqual(plan.notices, ['A flagset was played twice, so Council decides Game 3'])

    def test_a_chosen_game_repeats_the_roll_line(self):
        plan = tr.plan_for_room(state('defer', chosen={1: 'TTP5 Uphill Battle'}), 1, **CONTEXT)
        self.assertEqual(plan.opening[1], "Game 1 is Uphill Battle. Roll it when everyone's ready: !flags " + UPHILL)


class Commands(unittest.TestCase):
    def test_defer_hands_game_1_over(self):
        reply = tr.reply_to_command(state(), 1, 'thomjay', 'defer', flagsets=FLAGSETS)
        self.assertEqual(reply.text, "specialk3782 picks Game 1 and thomjay picks Game 2. specialk3782, "
                                     "when you're ready: !ttp5uphill !ttp5muffle !ttp5pick5")
        self.assertEqual(reply.op, ('decidePick', {'matchId': 'W1-25', 'choice': 'defer',
                                                   'actor': 'thomjay', 'by': 'racer'}))

    def test_pick_keeps_game_1(self):
        reply = tr.reply_to_command(state(), 1, 'thomjay', 'pick', flagsets=FLAGSETS)
        self.assertTrue(reply.text.startswith('thomjay picks Game 1 and specialk3782 picks Game 2. thomjay,'))
        self.assertEqual(reply.op[1]['choice'], 'pick')

    def test_only_the_better_seed_can_pick_or_defer(self):
        for who in ('specialk3782', None, 'someone'):
            reply = tr.reply_to_command(state(), 1, who, 'pick', flagsets=FLAGSETS)
            self.assertIsNone(reply.op)
            self.assertEqual(reply.text, 'Only thomjay, the better seed, decides who picks first.')

    def test_a_decision_after_game_1_starts_is_council_s(self):
        reply = tr.reply_to_command(state(games={1: 'TTP5 Uphill Battle'}), 1, 'thomjay', 'pick', flagsets=FLAGSETS)
        self.assertIsNone(reply.op)
        reply = tr.reply_to_command(state(), 2, 'thomjay', 'defer', flagsets=FLAGSETS)
        self.assertIsNone(reply.op)

    def test_flagset_commands_answer_only_the_owner_from_what_is_left(self):
        game2 = state('defer', games={1: 'TTP5 Uphill Battle'})
        self.assertEqual(tr.reply_to_command(game2, 2, 'specialk3782', 'ttp5pick5', flagsets=FLAGSETS),
                         tr.Reply("This one is thomjay's pick.", None))
        played = tr.reply_to_command(game2, 2, 'thomjay', 'ttp5uphill', flagsets=FLAGSETS)
        self.assertIsNone(played.op)
        self.assertEqual(played.text, "Uphill Battle has been played. When you're ready: !ttp5muffle !ttp5pick5")
        chosen = tr.reply_to_command(game2, 2, 'thomjay', 'TTP5Muffle', flagsets=FLAGSETS)
        self.assertEqual(chosen.text, "Game 2 is Muffle Rug. Roll it when everyone's ready: !flags "
                                      + MUFFLE + " If there's a Game 3, it's Pick 5.")
        self.assertEqual(chosen.op, ('choosePick', {'matchId': 'W1-25', 'game': 2, 'flagset': 'TTP5 Muffle Rug',
                                                    'actor': 'thomjay', 'by': 'racer'}))

    def test_game_1_choice_after_a_defer(self):
        reply = tr.reply_to_command(state('defer'), 1, 'specialk3782', 'ttp5uphill', flagsets=FLAGSETS)
        self.assertEqual(reply.text, "Game 1 is Uphill Battle. Roll it when everyone's ready: !flags " + UPHILL)

    def test_no_flagset_before_the_decision(self):
        reply = tr.reply_to_command(state(), 1, 'thomjay', 'ttp5uphill', flagsets=FLAGSETS)
        self.assertEqual(reply, tr.Reply('thomjay decides first: !pick or !defer.', None))

    def test_game_3_takes_no_pick(self):
        reply = tr.reply_to_command(state('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}),
                                    3, 'thomjay', 'ttp5uphill', flagsets=FLAGSETS)
        self.assertEqual(reply, tr.Reply('Game 3 is Pick 5, the flagset left, so nobody picks.', None))

    def test_other_commands_are_not_answered(self):
        self.assertEqual(tr.reply_to_command(state(), 1, 'thomjay', 'help', flagsets=FLAGSETS), tr.Reply(None, None))


class RollChecks(unittest.TestCase):
    def test_a_wrong_roll_names_what_the_game_should_be(self):
        check = tr.check_roll(UPHILL, state('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}), 3,
                              flagsets=FLAGSETS)
        self.assertFalse(check.ok)
        self.assertEqual(tr.with_council(check.warning, True),
                         'That seed is Uphill Battle, which was already played. This game should be Pick 5. '
                         '!cancel clears it, and Council has been told.')
        self.assertEqual(tr.with_council(check.warning, False),
                         'That seed is Uphill Battle, which was already played. This game should be Pick 5. '
                         "!cancel clears it. I couldn't reach Council, so please tell them in Discord.")

    def test_the_right_roll_is_ok(self):
        check = tr.check_roll(PICK5, state('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}), 3,
                              flagsets=FLAGSETS)
        self.assertEqual(check, tr.RollCheck(True, 'TTP5 Pick 5', None, None))

    def test_a_roll_other_than_the_choice_warns(self):
        check = tr.check_roll(PICK5, state('defer', chosen={1: 'TTP5 Uphill Battle'}), 1, flagsets=FLAGSETS)
        self.assertEqual(check.warning, 'That seed is Pick 5, but this game should be Uphill Battle. !cancel clears it')

    def test_free_text_flags_never_count(self):
        check = tr.check_roll('leagueweek8', state('defer'), 1, flagsets=FLAGSETS)
        self.assertFalse(check.ok)
        self.assertIsNone(check.flagset)
        self.assertEqual(check.warning, "That seed isn't one of this tournament's flagsets, so it won't count. !cancel clears it")

    def test_a_registry_string_not_on_the_frozen_list_warns(self):  # Review Focus 5
        check = tr.check_roll('143oNtDD4Pw3Yw6SRryTCXlqTsRz2fxg4s4pJX', state('defer', chosen={1: 'TTP5 Uphill Battle'}), 1,
                              flagsets=FLAGSETS, registry_name='TTP5 Uphill Battle')
        self.assertFalse(check.ok)
        self.assertEqual(check.warning, "That seed is a different Uphill Battle string from the one frozen for this "
                                        "tournament, so it won't count until Council confirms the change. This game "
                                        "should be Uphill Battle. !cancel clears it")

    def test_a_roll_with_no_decision_asks_the_better_seed(self):  # Codex requirement 2
        check = tr.check_roll(UPHILL, state(), 1, flagsets=FLAGSETS)
        self.assertTrue(check.ok)
        self.assertEqual(check.ask, 'thomjay, this seed was rolled before a !pick or !defer. Please confirm '
                                    'with !pick or !defer, so Game 2 has a picker.')

    def test_a_cancel_warns_whatever_race_info_says(self):
        for flags in (UPHILL, None):
            check = tr.check_roll(flags, state('defer'), 1, flagsets=FLAGSETS, state='cancelled')
            self.assertFalse(check.ok)
            self.assertEqual(check.warning, tr.CANCELLED_WARNING)
        self.assertEqual(tr.check_roll(UPHILL, state('defer'), 1, flagsets=FLAGSETS, state='none'),
                         tr.RollCheck(False, None, None, None))

    def test_a_rematch_room_does_not_count_its_own_game_as_played(self):  # Review Focus 2
        check = tr.check_roll(MUFFLE, state('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}), 2,
                              flagsets=FLAGSETS)
        self.assertTrue(check.ok)


class Reminders(unittest.TestCase):
    def test_only_while_a_pick_is_owed(self):
        self.assertEqual(tr.reminder(state(), 1, 15, flagsets=FLAGSETS),
                         'thomjay, the race starts in 15 minutes. Decide with !pick or !defer.')
        self.assertEqual(tr.reminder(state('defer'), 1, 10, flagsets=FLAGSETS),
                         "specialk3782, the race starts in 10 minutes and this game's pick is still yours: "
                         '!ttp5uphill !ttp5muffle !ttp5pick5')
        self.assertIsNone(tr.reminder(state('defer', chosen={1: 'TTP5 Uphill Battle'}), 1, 10, flagsets=FLAGSETS))
        self.assertIsNone(tr.reminder(state('defer'), 1, 10, flagsets=FLAGSETS, rolled=True))
        self.assertIsNone(tr.reminder(state('defer', games={1: 'a', 2: 'b'}), 3, 15, flagsets=FLAGSETS))


if __name__ == '__main__':
    unittest.main()
