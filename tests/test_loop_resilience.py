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

    def test_survives_http_and_socket_level_faults(self):
        for exception in (
            aiohttp.ClientError('racetime unreachable'),
            asyncio.TimeoutError(),
            OSError('connection reset'),
        ):
            with self.subTest(exception=type(exception).__name__):
                bot, loop = _bot(), _Loop()
                bot.handle_exception(loop, {'exception': exception})
                self.assertFalse(loop.stopped)

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
