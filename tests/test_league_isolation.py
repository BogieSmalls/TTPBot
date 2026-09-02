"""Isolation tests: TTP behavior is unchanged by League scheduling addition.

After adding League scheduling to TTPBot, these tests verify that:
- TTP configuration constants remain exactly as they were
- TTP room creation form fields remain unchanged
- Both TTP and League entry kinds are properly registered in state
"""

import unittest
from datetime import datetime

from ttpbot import bot as bot_module
from ttpbot.config import (
    GOAL_NAME,
    POST_SEASON_GOAL_NAME,
    REMINDER_SCHEDULE,
    ROOM_OPEN_MINUTES_BEFORE,
    TIMEZONE,
    WEBHOOK_MINUTES_BEFORE,
)
from ttpbot.league.scheduler import LeagueScheduler
from ttpbot.state import ENTRY_KINDS


class TtpIsolationTests(unittest.TestCase):
    def test_ttp_constants_are_unchanged(self):
        self.assertEqual(GOAL_NAME, 'TTP Season 5')
        self.assertEqual(POST_SEASON_GOAL_NAME, 'Beat the game')
        self.assertEqual(ROOM_OPEN_MINUTES_BEFORE, 30)
        self.assertEqual(WEBHOOK_MINUTES_BEFORE, 20)
        self.assertEqual(len(REMINDER_SCHEDULE), 4)

    def test_ttp_room_form_is_unchanged(self):
        form = bot_module.race_room_form_data(
            datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE))

        self.assertEqual(form['goal'], GOAL_NAME)
        self.assertIn('TTP Season 5 | Scheduled:', form['info_bot'])
        self.assertEqual(form['invitational'], 'false')
        self.assertEqual(form['streaming_required'], 'true')

    def test_league_uses_separate_state_files(self):
        source = LeagueScheduler.__module__
        self.assertTrue(source.startswith('ttpbot.league'))
        self.assertIn('league_created_races', ENTRY_KINDS)
        self.assertIn('league_sent_webhooks', ENTRY_KINDS)
        self.assertIn('created_races', ENTRY_KINDS)
        self.assertIn('sent_webhooks', ENTRY_KINDS)


if __name__ == '__main__':
    unittest.main()
