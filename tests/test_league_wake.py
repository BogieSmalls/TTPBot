import logging
import unittest
from unittest.mock import Mock

from ttpbot.league.wake import wake_control_plane

QUIET = logging.getLogger('test-league-wake')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


class _Response:
    def __init__(self, status, text=''):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class WakeControlPlaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_bearer_token_the_relay_requires(self):
        seen = {}

        def request(**kwargs):
            seen.update(kwargs)
            return _Response(200)

        await wake_control_plane('http://127.0.0.1:3005', QUIET, token='ci-tok', requester=request)

        # /api/wake checks tokens.ciWake and answers 401 without it, so a
        # tokenless wake never wakes anything.
        self.assertEqual(seen['headers']['Authorization'], 'Bearer ci-tok')
        self.assertEqual(seen['url'], 'http://127.0.0.1:3005/api/wake')
        self.assertEqual(seen['json'], {'target': 'production'})

    async def test_wakes_the_target_it_is_given(self):
        seen = {}

        def request(**kwargs):
            seen.update(kwargs)
            return _Response(200)

        await wake_control_plane(
            'http://127.0.0.1:3005', QUIET, token='t', target='staging', requester=request,
        )

        # A rehearsal must be able to wake staging rather than production.
        self.assertEqual(seen['json'], {'target': 'staging'})

    async def test_raises_on_an_unauthorized_wake(self):
        with self.assertRaises(RuntimeError):
            await wake_control_plane(
                'http://127.0.0.1:3005', QUIET, token='wrong',
                requester=lambda **k: _Response(401, '{"ok":false,"error":"unauthorized"}'),
            )

    async def test_refuses_to_call_without_a_token(self):
        called = []

        with self.assertRaises(RuntimeError):
            await wake_control_plane(
                'http://127.0.0.1:3005', QUIET, token='',
                requester=lambda **k: called.append(k),
            )

        # Better to fail loudly here than to send a request that cannot work.
        self.assertEqual(called, [])
