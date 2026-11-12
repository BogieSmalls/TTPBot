import logging
import unittest
from unittest.mock import Mock

from ttpbot.league.booth import fetch_booking_features, request_booth, send_heads_up

QUIET = logging.getLogger('test-league-booth')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False

PAYLOAD = {'leagueKey': 'k1', 'twitchChannel': 'Z1Rracing'}


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def requester(response, seen=None):
    def _request(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        return response
    return _request


def routed(answers, calls):
    """Answers by URL fragment, 404 otherwise; records each call."""
    def _request(**kwargs):
        calls.append(kwargs)
        for fragment, response in answers.items():
            if fragment in kwargs['url']:
                return response
        return _Response(404, {})
    return _request


class HeadsUpTests(unittest.IsolatedAsyncioTestCase):
    async def test_heads_up_is_not_sent_to_a_control_plane_that_does_not_advertise_it(self):
        calls = []
        result = await send_heads_up(PAYLOAD, 'https://cp.example', 'tok', QUIET, requester=routed({}, calls),
                                     endpoint='/internal/relay/league/broadcast', label='League')

        # An older control plane would stage a booth on a heads-up.
        self.assertIsNone(result)
        self.assertEqual([call['url'] for call in calls], ['https://cp.example/internal/relay/booking-features'])

    async def test_heads_up_carries_prepare_only_and_no_room(self):
        calls = []
        answers = {
            '/booking-features': _Response(200, {'prepareOnly': True, 'slate': True}),
            '/league/broadcast': _Response(200, {'outcome': 'next_race', 'draftId': 'd1', 'broadcastId': 'b0', 'prepared': True}),
        }
        result = await send_heads_up(PAYLOAD, 'https://cp.example', 'tok', QUIET, requester=routed(answers, calls),
                                     endpoint='/internal/relay/league/broadcast', label='League')

        self.assertEqual(result.outcome, 'next_race')
        self.assertEqual(calls[0]['method'], 'get')
        self.assertEqual(calls[0]['headers']['Authorization'], 'Bearer tok')
        self.assertEqual(calls[-1]['url'], 'https://cp.example/internal/relay/league/broadcast')
        self.assertEqual(calls[-1]['json'], {**PAYLOAD, 'prepareOnly': True})
        self.assertNotIn('raceSlug', calls[-1]['json'])

    async def test_booking_features_keep_only_the_flags_that_are_true(self):
        answers = {'/booking-features': _Response(200, {'prepareOnly': True, 'slate': False, 'other': 'yes'})}
        self.assertEqual(await fetch_booking_features('https://cp.example', 'tok', QUIET, requester=routed(answers, [])),
                         frozenset({'prepareOnly'}))
        self.assertEqual(await fetch_booking_features('https://cp.example', 'tok', QUIET, requester=routed({}, [])),
                         frozenset())


class RequestBoothTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_a_staged_booth(self):
        seen = {}
        result = await request_booth(
            PAYLOAD, 'https://cp.example', 'tok', QUIET,
            requester=requester(_Response(200, {'outcome': 'staged', 'broadcastId': 'b1'}), seen),
        )

        self.assertEqual(result.outcome, 'staged')
        self.assertEqual(seen['url'], 'https://cp.example/internal/relay/league/broadcast')
        self.assertEqual(seen['headers']['Authorization'], 'Bearer tok')

    async def test_reports_a_continuation(self):
        result = await request_booth(
            PAYLOAD, 'https://cp.example', 'tok', QUIET,
            requester=requester(_Response(200, {'outcome': 'continuation', 'broadcastId': 'b0'})),
        )

        # The announcement has to carry the already-on-air warning, and only
        # the control plane knows this is a continuation.
        self.assertEqual(result.outcome, 'continuation')

    async def test_next_race_reads_as_continuation(self):
        result = await request_booth(
            PAYLOAD, 'https://cp.example', 'tok', QUIET,
            requester=requester(_Response(200, {'outcome': 'next_race', 'draftId': 'd1', 'broadcastId': 'b0'})),
        )

        # The race is the booth on air's next one: the channel is already on air.
        self.assertIs(result.is_continuation, True)
        self.assertEqual(result.broadcast_id, 'b0')

    async def test_unwraps_an_already_done_replay(self):
        result = await request_booth(
            PAYLOAD, 'https://cp.example', 'tok', QUIET,
            requester=requester(_Response(200, {
                'outcome': 'already_done',
                'result': {'outcome': 'continuation', 'broadcastId': 'b0'},
            })),
        )

        # A retry must announce what the original call actually did, not the
        # fact that it was a retry.
        self.assertEqual(result.outcome, 'continuation')
        self.assertEqual(result.broadcast_id, 'b0')

    async def test_treats_a_replay_with_no_stored_result_as_unknown(self):
        result = await request_booth(
            PAYLOAD, 'https://cp.example', 'tok', QUIET,
            requester=requester(_Response(200, {'outcome': 'already_done', 'result': None})),
        )

        self.assertIsNone(result.outcome)

    async def test_a_refused_request_is_not_an_outcome(self):
        logger = Mock()

        result = await request_booth(
            PAYLOAD, 'https://cp.example', 'tok', logger,
            requester=requester(_Response(409, {'error': 'stage_failed', 'reason': 'x'})),
        )

        self.assertIsNone(result.outcome)
        self.assertEqual(result.refusal, 'x')
        self.assertTrue(logger.warning.called)

    async def test_survives_the_control_plane_being_unreachable(self):
        def boom(**_):
            raise OSError('connection refused')

        result = await request_booth(
            PAYLOAD, 'https://cp.example', 'tok', Mock(), requester=boom,
        )

        # The announcement still has to go out, so this can never raise.
        self.assertIsNone(result.outcome)

    async def test_does_nothing_without_configuration(self):
        called = []

        result = await request_booth(
            PAYLOAD, '', '', QUIET, requester=lambda **k: called.append(k),
        )

        self.assertIsNone(result.outcome)
        self.assertEqual(called, [])


if __name__ == '__main__':
    unittest.main()
