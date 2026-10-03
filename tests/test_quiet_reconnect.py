import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from ttpbot.config import TIMEZONE
from ttpbot.handler import TTPRaceHandler

from tests.test_handler_commands import FakeWebSocket, command_handler

LIVE = {'value': 'in_progress'}


def live_handler():
    handler = command_handler()
    handler.state = {}
    handler.reminders_sent = set()
    handler.reminder_task = None
    handler.grace_ledger = None
    handler.ws = FakeWebSocket()
    handler.data = {'name': 'z1r/live-room', 'status': LIVE, 'info_bot': ''}
    return handler


class QuietReconnectTests(unittest.IsolatedAsyncioTestCase):
    """A deploy mid-race reconnects to rooms full of racers: say nothing."""

    async def test_no_welcome_when_the_old_one_scrolled_out_of_history(self):
        # 2026-09-26: a restart re-posted the welcome in the 8 PM race.
        handler = live_handler()
        handler.ttp_scheduled_room = True

        await handler.chat_history({'messages': []})

        self.assertEqual(handler.messages, [])
        self.assertTrue(handler.state['welcomed'])

    async def test_an_open_room_is_still_welcomed(self):
        handler = live_handler()
        handler.data['status'] = {'value': 'open'}
        handler.ttp_scheduled_room = True

        await handler.chat_history({'messages': []})

        self.assertEqual(len(handler.messages), 1)

    async def test_no_reminders_for_a_race_that_started_early(self):
        handler = live_handler()
        soon = datetime.now(TIMEZONE) + timedelta(seconds=30)
        handler.data['goal'] = {'name': 'TTP Season 5'}

        with patch('ttpbot.handler.is_ttp_scheduled_room', return_value=True), \
                patch.object(TTPRaceHandler, '_determine_scheduled_time',
                             lambda self: setattr(self, 'scheduled_time', soon)):
            await handler.begin()

        self.assertIsNone(handler.reminder_task)

    async def test_no_invites_into_a_live_tournament_race(self):
        handler = live_handler()
        invites = AsyncMock()
        handler._send_autumn_invites = invites

        with patch('ttpbot.handler.is_autumn_room', return_value=True):
            await handler.begin()

        invites.assert_not_called()


if __name__ == '__main__':
    unittest.main()
