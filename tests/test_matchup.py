import unittest
from unittest.mock import patch

from ttpbot.matchup import clean_name, format_matchup, matchup_reply

from tests.test_handler_commands import command_handler

BOGIE = {'id': 'rt-raP6yoan7KWlV4zN', 'name': 'Bogie'}
DROOIS = {'id': 'rt-NqO2YoLLAbo9QEya', 'name': 'Droois'}
# Real figures for Bogie vs Droois from api.z1rracing.com on 2026-09-26.
SUMMARY = {'common': 114, 'ahead': {'a': 70, 'b': 44}, 'oneOnOnes': {'total': 0, 'a': 0, 'b': 0}}


def fake_api(racers, summary=SUMMARY, down=False):
    """A fetch that answers name searches by substring, as the real API does."""
    calls = []

    async def fetch(path):
        calls.append(path)
        if down:
            return None
        if path.startswith('/v1/racers?q='):
            q = path.split('=', 1)[1].lower()
            return {'racers': [r for r in racers if q in r['name'].lower()]}
        return {'summary': summary}

    fetch.calls = calls
    return fetch


class FormatTests(unittest.TestCase):
    def test_reports_all_three_figures(self):
        self.assertEqual(
            format_matchup('Bogie', 'Droois', SUMMARY),
            'Bogie vs Droois: 114 mutual races, Bogie ahead 70-44, '
            'and they have never raced head-to-head.',
        )

    def test_head_to_head_wording(self):
        def say(one, ahead=None):
            return format_matchup('A', 'B', {
                'common': 10, 'ahead': ahead or {'a': 5, 'b': 5}, 'oneOnOnes': one,
            })

        self.assertIn('level 5-5', say({'total': 0, 'a': 0, 'b': 0}))
        self.assertIn('1 head-to-head, won by B', say({'total': 1, 'a': 0, 'b': 1}))
        self.assertIn('4 head-to-heads, split 2-2', say({'total': 4, 'a': 2, 'b': 2}))
        self.assertIn('5 head-to-heads, B leading 3-2', say({'total': 5, 'a': 2, 'b': 3}))
        self.assertIn('B ahead 6-4', say({'total': 0, 'a': 0, 'b': 0}, {'a': 4, 'b': 6}))

    def test_no_shared_races(self):
        self.assertEqual(
            format_matchup('A', 'B', {'common': 0, 'ahead': {'a': 0, 'b': 0},
                                      'oneOnOnes': {'total': 0, 'a': 0, 'b': 0}}),
            'A vs B: no shared races yet.',
        )

    def test_clean_name_drops_chat_decoration(self):
        self.assertEqual(clean_name('@Crump#8654'), 'Crump')


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_looks_up_both_and_uses_their_real_names(self):
        fetch = fake_api([BOGIE, DROOIS])

        reply = await matchup_reply('bogie', 'DROOIS', fetch)

        self.assertTrue(reply.startswith('Bogie vs Droois: 114 mutual races'))
        self.assertIn(f"/v1/matchups/{BOGIE['id']}/{DROOIS['id']}", fetch.calls)

    async def test_exact_name_wins_over_substring_matches(self):
        # The search matches substrings: "Bort" also finds "Bortle".
        bort = {'id': 'rt-bort', 'name': 'Bort'}
        fetch = fake_api([bort, {'id': 'rt-bortle', 'name': 'Bortle'}, BOGIE])

        reply = await matchup_reply('bort', 'bogie', fetch)

        self.assertTrue(reply.startswith('Bort vs Bogie:'))

    async def test_accepts_a_lone_partial_match(self):
        reply = await matchup_reply('drooi', 'bogie', fake_api([BOGIE, DROOIS]))
        self.assertTrue(reply.startswith('Droois vs Bogie:'))

    async def test_refuses_an_ambiguous_name(self):
        racers = [BOGIE, {'id': 'rt-bort', 'name': 'Bort'}, DROOIS]
        reply = await matchup_reply('bo', 'droois', fake_api(racers))
        self.assertEqual(reply, '"bo" matches more than one racer: Bogie, Bort')

    async def test_unknown_racer(self):
        reply = await matchup_reply('nobody', 'bogie', fake_api([BOGIE]))
        self.assertEqual(reply, 'No racer named nobody in the Z1RR stats.')

    async def test_same_racer_twice(self):
        reply = await matchup_reply('bogie', 'Bogie', fake_api([BOGIE]))
        self.assertEqual(reply, 'Bogie vs Bogie: pick two different racers.')

    async def test_says_so_when_the_api_is_down(self):
        reply = await matchup_reply('bogie', 'droois', fake_api([], down=True))
        self.assertIn("Couldn't reach the Z1RR stats", reply)

    async def test_malformed_summary_is_not_reported(self):
        reply = await matchup_reply('bogie', 'droois', fake_api([BOGIE, DROOIS], {'common': 3}))
        self.assertEqual(reply, "Couldn't read the Z1RR stats for that matchup right now.")


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_answers_even_with_sahasrahbot_present(self):
        handler = command_handler()
        handler.sahasrahbot_present = True

        with patch('ttpbot.matchup.fetch_json', new=fake_api([BOGIE, DROOIS])):
            await handler.ex_matchup(['bogie', 'droois'], {})

        self.assertEqual(len(handler.messages), 1)
        self.assertTrue(handler.messages[0].startswith('Bogie vs Droois:'))

    async def test_needs_exactly_two_racers(self):
        handler = command_handler()

        await handler.ex_matchup(['bogie'], {})

        self.assertEqual(handler.messages, ['Usage: !matchup <racer1> <racer2>'])

    async def test_listed_in_help(self):
        handler = command_handler()

        await handler.ex_help([], {})

        self.assertIn('!matchup <racer1> <racer2>', handler.messages[0])


if __name__ == '__main__':
    unittest.main()
