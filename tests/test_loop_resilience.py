"""The bot must outlive racetime having a bad thirty seconds.

On 2026-09-07 a transient 502 on a race-room websocket handshake stopped the
event loop. The process exited 0, systemd's `Restart=on-failure` correctly did
nothing, and the bot stayed down for two minutes with a League race half an
hour out. Nothing was wrong with our code or theirs; the loop simply had no
tolerance for a connection that was refused rather than dropped.
"""

import asyncio
import unittest
from unittest.mock import Mock

import aiohttp
import requests.exceptions
import websockets.exceptions

from ttpbot.bot import TTPBot


class _Loop:
    """Records whether anything asked the event loop to stop."""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def _bot():
    bot = TTPBot.__new__(TTPBot)
    bot.logger = Mock()
    return bot


def _invalid_status(code):
    """The exception websockets raises when the handshake is refused."""
    response = websockets.datastructures.Headers()
    return websockets.exceptions.InvalidStatus(
        Mock(status_code=code, headers=response),
    )


class TransientErrorsDoNotStopTheLoop(unittest.TestCase):
    def test_survives_the_502_handshake_that_took_the_bot_down(self):
        bot, loop = _bot(), _Loop()

        bot.handle_exception(loop, {
            'message': 'Task exception was never retrieved',
            'exception': _invalid_status(502),
        })

        # This is the exact shape of the 2026-09-07 outage.
        self.assertFalse(loop.stopped)

    def test_survives_a_socket_that_closed_after_connecting(self):
        bot, loop = _bot(), _Loop()

        bot.handle_exception(loop, {
            'exception': websockets.exceptions.ConnectionClosedError(None, None),
        })

        self.assertFalse(loop.stopped)

    def test_does_not_swallow_faults_the_loop_cannot_recover_from(self):
        # Only a websocket failure self-heals: refresh_races rebuilds the
        # handler within a scan cycle. Nothing else does, and suppressing
        # these would leave a dead task inside a process systemd still calls
        # healthy. Stopping the loop is the better outcome now that the unit
        # restarts on `always`.
        for exception in (
            aiohttp.ClientError('racetime unreachable'),
            asyncio.TimeoutError(),
            OSError('connection reset'),
        ):
            with self.subTest(exception=type(exception).__name__):
                bot, loop = _bot(), _Loop()
                bot.handle_exception(loop, {'exception': exception})
                self.assertTrue(loop.stopped)

    def test_a_token_refresh_failure_is_never_suppressed(self):
        # racetime_bot.authorize() uses requests, and every requests exception
        # inherits OSError. reauthorize() has no try/except of its own, so
        # suppressing this would kill the token refresh for the life of the
        # process while leaving the bot apparently fine.
        bot, loop = _bot(), _Loop()

        bot.handle_exception(loop, {
            'message': 'Task exception was never retrieved',
            'exception': requests.exceptions.ConnectionError('token endpoint down'),
        })

        self.assertTrue(loop.stopped)

    def test_websocket_misuse_still_stops_the_loop(self):
        # Both inherit WebSocketException but mean the socket was used wrongly.
        # Tolerating them would spin rather than recover.
        for exception in (
            websockets.exceptions.InvalidState('unexpected frame'),
            websockets.exceptions.ConcurrencyError('two readers'),
        ):
            with self.subTest(exception=type(exception).__name__):
                bot, loop = _bot(), _Loop()
                bot.handle_exception(loop, {'exception': exception})
                self.assertTrue(loop.stopped)

    def test_matches_by_isinstance_not_exact_class(self):
        class NewerWebsocketsFailure(websockets.exceptions.InvalidHandshake):
            """Stand-in for a websockets release adding a subclass."""

        bot, loop = _bot(), _Loop()

        bot.handle_exception(loop, {'exception': NewerWebsocketsFailure()})

        # The library compares `exception.__class__ not in continue_on`, so a
        # subclass reopens the hole. Ours must not.
        self.assertFalse(loop.stopped)


class RealFailuresStillStopTheLoop(unittest.TestCase):
    def test_a_programming_error_still_stops_the_loop(self):
        bot, loop = _bot(), _Loop()

        bot.handle_exception(loop, {'exception': TypeError('bad code')})

        # Tolerating everything would turn a crash into a silent wedge.
        self.assertTrue(loop.stopped)

    def test_a_context_with_no_exception_still_stops_the_loop(self):
        bot, loop = _bot(), _Loop()

        bot.handle_exception(loop, {'message': 'something went wrong'})

        self.assertTrue(loop.stopped)
