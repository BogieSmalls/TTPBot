import asyncio
import json
import logging
import unittest
from unittest.mock import AsyncMock

from ttpbot.config import POST_SEASON_GOAL_NAME
from ttpbot.handler import TTPRaceHandler

QUIET = logging.getLogger('test-league-handler')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False

LEAGUE_DATA = {
    'name': 'z1r/clever-slug-1234',
    'goal': {'name': POST_SEASON_GOAL_NAME},
    'info_bot': 'League: SirLinkalot vs. Windfox470',
}


def make_handler(state):
    handler = object.__new__(TTPRaceHandler)
    handler.logger = QUIET
    handler.state = state
    handler.ws = AsyncMock()
    handler.invite_user = AsyncMock()
    # Initialise only the attributes begin() touches.
    handler.reminders_sent = set()
    handler.scheduled_time = None
    handler.bot_created = False
    handler.ttp_scheduled_room = False
    handler.reminder_task = None
    handler.league_room = False
    handler.league_invited = False
    handler.sahasrahbot_present = False
    handler.seed_rolled = False
    handler.history_command_cutoff_utc = None
    handler.data = LEAGUE_DATA
    return handler


class LeagueInviteTests(unittest.IsolatedAsyncioTestCase):
    async def test_invites_both_seeded_racers(self):
        handler = make_handler({'league_race': {'invite': ['rt-sir', 'rt-wind']}})

        await handler.begin()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['rt-sir', 'rt-wind'],
        )

    async def test_does_not_invite_twice_on_reconnect(self):
        handler = make_handler({'league_race': {'invite': ['rt-sir', 'rt-wind']}})

        await handler.begin()
        await handler.begin()

        self.assertEqual(handler.invite_user.await_count, 2)

    async def test_schedules_no_reminders_in_a_league_room(self):
        handler = make_handler({'league_race': {'invite': ['rt-sir', 'rt-wind']}})

        await handler.begin()

        self.assertFalse(handler.ttp_scheduled_room)
        self.assertIsNone(handler.reminder_task)
        self.assertIsNone(handler.scheduled_time)

    async def test_still_requests_chat_history_for_seed_detection(self):
        handler = make_handler({'league_race': {'invite': ['rt-sir', 'rt-wind']}})

        await handler.begin()

        sent = [json.loads(c.args[0]) for c in handler.ws.send.await_args_list]
        self.assertIn('gethistory', [message.get('action') for message in sent])

    async def test_recovers_invites_from_the_title_after_a_restart(self):
        # A restart between room creation and joining loses the seeded state.
        handler = make_handler({})

        await handler.begin()

        self.assertEqual(handler.invite_user.await_count, 2)

    async def test_invites_nobody_when_the_title_cannot_be_resolved(self):
        handler = make_handler({})
        handler.data = dict(LEAGUE_DATA, info_bot='League: Nobody vs. AlsoNobody')

        await handler.begin()

        handler.invite_user.assert_not_awaited()

    async def test_ttp_rooms_are_untouched(self):
        handler = make_handler({})
        handler.data = {
            'name': 'z1r/ttp-room',
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_bot': 'Triforce Triple Play | Scheduled: Mon Dec 21, 08:00 PM EST',
        }

        await handler.begin()

        handler.invite_user.assert_not_awaited()
        self.assertTrue(handler.ttp_scheduled_room)


if __name__ == '__main__':
    unittest.main()
