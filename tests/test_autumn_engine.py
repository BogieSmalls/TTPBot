"""Talking to the bracket engine, and what happens when it does not answer.

The mirror write is the one that matters. The Schedule tab owns when a race is,
so a failed mirror is a nuisance -- but a mirror believed when it did not happen
is a room at a time nobody agreed to. Three outcomes, not two, and the lost
answer is settled by reading back rather than by sending again.
"""

import asyncio
from datetime import datetime, timezone
import json as jsonlib
import unittest

import aiohttp

from ttpbot.autumn.engine import (
    NOT_RECORDED,
    RECORDED,
    UNCONFIRMED,
    AutumnEngine,
    EngineUnreachable,
    engine_from_env,
)

AT = datetime(2026, 10, 2, 22, 0, tzinfo=timezone.utc)


class Answer:
    """One canned HTTP response, as an async context manager."""

    def __init__(self, status=200, body='{}', raises=None):
        self.status = status
        self._body = body
        self._raises = raises

    async def text(self):
        return self._body

    async def __aenter__(self):
        if self._raises:
            raise self._raises
        return self

    async def __aexit__(self, *exc):
        return False


class Engine:
    """An engine that answers from a script, and records what it was asked."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.asked = []

    def __call__(self, method, url, headers=None, json=None):
        self.asked.append({
            'method': method, 'url': url, 'headers': headers or {}, 'json': json,
        })
        if not self._answers:
            raise AssertionError('nothing scripted for {} {}'.format(method, url))
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            return Answer(raises=answer)
        return answer


def run(coro):
    return asyncio.run(coro)


def engine(*answers, token='secret'):
    scripted = Engine(*answers)
    return AutumnEngine(token=token, session_factory=scripted), scripted


class ReadingTheDraw(unittest.TestCase):
    def test_the_draw_comes_back_with_its_aliases(self):
        it, asked = engine(Answer(body=jsonlib.dumps({
            'drawn': True,
            'matches': [{'id': 'W1-1', 'a': 'ISUMatt', 'b': 'chessjerk', 'state': 'ready'}],
            'aliases': {'RhjnoHero': 'RhinoHero'},
        })))
        drawn = run(it.draw())
        self.assertEqual(drawn['aliases'], {'RhjnoHero': 'RhinoHero'})
        # The public draw needs no token, and must not be sent one.
        self.assertNotIn('authorization', asked.asked[0]['headers'])
        self.assertIn('event=autumn', asked.asked[0]['url'])

    def test_matches_are_keyed_by_id_for_the_matcher(self):
        it, _ = engine(Answer(body=jsonlib.dumps({
            'matches': [
                {'id': 'W1-1', 'a': 'A', 'b': 'B', 'state': 'ready'},
                {'id': 'GF-2', 'a': None, 'b': None, 'state': 'not-needed'},
            ],
        })))
        found = run(it.matches())
        self.assertEqual(sorted(found), ['GF-2', 'W1-1'])
        self.assertEqual(found['W1-1']['state'], 'ready')

    def test_a_200_that_is_not_json_is_not_an_empty_draw(self):
        # Something else answering on that port. Treating it as an empty draw
        # would draw a bracket nobody is racing.
        it, _ = engine(Answer(body='<html>nope</html>'))
        with self.assertRaises(EngineUnreachable) as caught:
            run(it.draw())
        self.assertEqual(caught.exception.outcome, UNCONFIRMED)

    def test_a_5xx_is_nobody_knowing_and_a_4xx_is_an_answer(self):
        it, _ = engine(Answer(status=503, body='{"error":"unreadable"}'))
        with self.assertRaises(EngineUnreachable) as caught:
            run(it.draw())
        self.assertEqual(caught.exception.outcome, UNCONFIRMED)
        self.assertEqual(caught.exception.status, 503)

        it, _ = engine(Answer(status=400, body='{"error":"not a competition"}'))
        with self.assertRaises(EngineUnreachable) as caught:
            run(it.draw())
        self.assertEqual(caught.exception.outcome, NOT_RECORDED)

    def test_the_state_read_carries_the_token(self):
        it, asked = engine(Answer(body=jsonlib.dumps({'document': {'revision': 4}})))
        state = run(it.state())
        self.assertEqual(state['document']['revision'], 4)
        self.assertEqual(asked.asked[0]['headers']['authorization'], 'Bearer secret')


class MirroringATime(unittest.TestCase):
    def test_a_recorded_time_is_recorded(self):
        it, asked = engine(Answer(body=jsonlib.dumps({'revision': 7})))
        written = run(it.mirror_time('W1-1', AT))
        self.assertTrue(written.ok)
        self.assertEqual(written.outcome, RECORDED)
        self.assertEqual(written.revision, 7)
        sent = asked.asked[0]
        self.assertEqual(sent['method'], 'POST')
        self.assertTrue(sent['url'].endswith('/tournament/time'))
        # The event rides on every write, and the time goes as an ISO instant.
        self.assertEqual(sent['json']['event'], 'autumn')
        self.assertEqual(sent['json']['at'], AT.isoformat())

    def test_a_refusal_is_final_and_is_not_read_back(self):
        # The engine looked at it and said no. Asking again gets the same no, and
        # reading back would only add a request to a settled answer.
        it, asked = engine(Answer(status=409, body='{"error":"there is no match W9-9"}'))
        written = run(it.mirror_time('W9-9', AT))
        self.assertEqual(written.outcome, NOT_RECORDED)
        self.assertIn('W9-9', written.detail)
        self.assertEqual(len(asked.asked), 1, 'a refusal must not be read back')

    def test_a_lost_answer_is_settled_by_reading_back(self):
        # The write may have landed. This is the case that must never be retried
        # blind, because the retry would be the second of two writes.
        it, asked = engine(
            aiohttp.ClientError('socket went away'),
            Answer(body=jsonlib.dumps({'document': {
                'revision': 9,
                'times': {'W1-1': {'at': AT.isoformat()}},
            }})),
        )
        written = run(it.mirror_time('W1-1', AT))
        self.assertEqual(written.outcome, RECORDED)
        self.assertEqual(written.revision, 9)
        self.assertIn('read-back', written.detail)
        # And exactly one retry-free read, not a second POST.
        self.assertEqual([a['method'] for a in asked.asked], ['POST', 'GET'])

    def test_a_read_back_that_finds_nothing_says_it_was_not_recorded(self):
        it, _ = engine(
            asyncio.TimeoutError(),
            Answer(body=jsonlib.dumps({'document': {'revision': 9, 'times': {}}})),
        )
        written = run(it.mirror_time('W1-1', AT))
        self.assertEqual(written.outcome, NOT_RECORDED)

    def test_a_read_back_that_finds_a_different_time_is_not_a_success(self):
        # Somebody else's write, or an older one. Either way this request did not
        # land, and saying it did would leave the sheet and the mirror disagreeing
        # with nobody looking.
        it, _ = engine(
            asyncio.TimeoutError(),
            Answer(body=jsonlib.dumps({'document': {
                'times': {'W1-1': {'at': '2026-10-09T22:00:00+00:00'}},
            }})),
        )
        self.assertEqual(run(it.mirror_time('W1-1', AT)).outcome, NOT_RECORDED)

    def test_a_failed_read_back_leaves_it_unconfirmed(self):
        # Nobody knows, and that is said plainly. The guess is wrong exactly when
        # it costs the most.
        it, _ = engine(
            asyncio.TimeoutError(),
            aiohttp.ClientError('still down'),
        )
        written = run(it.mirror_time('W1-1', AT))
        self.assertEqual(written.outcome, UNCONFIRMED)
        self.assertIn('read-back failed', written.detail)

    def test_a_5xx_on_the_write_is_read_back_too(self):
        it, asked = engine(
            Answer(status=500, body='could not write'),
            Answer(body=jsonlib.dumps({'document': {
                'revision': 3, 'times': {'W1-1': {'at': AT.isoformat()}},
            }})),
        )
        self.assertEqual(run(it.mirror_time('W1-1', AT)).outcome, RECORDED)
        self.assertEqual(len(asked.asked), 2)


class Threads(unittest.TestCase):
    def test_a_claim_and_a_record_go_to_their_own_operations(self):
        it, asked = engine(
            Answer(body='{"claim":"abc","threadId":null}'),
            Answer(body='{"threadId":"123"}'),
        )
        claimed = run(it.claim_thread('W1-1', by='ttpbot'))
        recorded = run(it.record_thread('W1-1', 123))
        self.assertTrue(claimed.ok and recorded.ok)
        self.assertTrue(asked.asked[0]['url'].endswith('/tournament/claimThread'))
        self.assertTrue(asked.asked[1]['url'].endswith('/tournament/thread'))
        # A thread id is a string everywhere, or the same thread reads as two.
        self.assertEqual(asked.asked[1]['json']['threadId'], '123')


class Configuration(unittest.TestCase):
    def test_no_token_means_the_runner_stays_off(self):
        # A relay without the token should run the League and say nothing about
        # Autumn, not fail every tick.
        self.assertIsNone(engine_from_env({}))
        self.assertIsNone(engine_from_env({'Z1RR_ENGINE_TOKEN': '   '}))

    def test_the_url_defaults_to_the_relay_port(self):
        it = engine_from_env({'Z1RR_ENGINE_TOKEN': 'secret'})
        self.assertEqual(it._url, 'http://127.0.0.1:3007')
        moved = engine_from_env({
            'Z1RR_ENGINE_TOKEN': 'secret',
            'Z1RR_ENGINE_URL': 'http://127.0.0.1:3117/',
        })
        self.assertEqual(moved._url, 'http://127.0.0.1:3117')

    def test_a_write_without_a_token_is_refused_before_it_is_sent(self):
        it, asked = engine(token=None)
        written = run(it.mirror_time('W1-1', AT))
        self.assertEqual(written.outcome, NOT_RECORDED)
        self.assertEqual(asked.asked, [], 'nothing should have been sent')


if __name__ == '__main__':
    unittest.main()
