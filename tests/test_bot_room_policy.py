import unittest
from datetime import datetime

from ttpbot.bot import is_ttp_scheduled_room, race_room_form_data
from ttpbot.config import GOAL_NAME, POST_SEASON_GOAL_NAME, TIMEZONE


class BotRoomPolicyTests(unittest.TestCase):
    def test_regular_season_form_data_uses_ttp5_goal(self):
        data = race_room_form_data(datetime(2026, 12, 19, 18, 0, tzinfo=TIMEZONE))

        self.assertEqual(GOAL_NAME, 'TTP: Season 5')
        self.assertEqual(data['goal'], GOAL_NAME)
        self.assertIn('TTP Season 5 | Scheduled:', data['info_bot'])

    def test_post_season_form_data_uses_beat_the_game_goal(self):
        data = race_room_form_data(datetime(2026, 12, 21, 20, 0, tzinfo=TIMEZONE))

        self.assertEqual(data['goal'], POST_SEASON_GOAL_NAME)
        self.assertIn('Triforce Triple Play | Scheduled:', data['info_bot'])

    def test_accepts_regular_season_goal_rooms(self):
        self.assertTrue(is_ttp_scheduled_room({
            'goal': {'name': GOAL_NAME},
            'info_bot': 'TTP Season 5 | Scheduled: Sat Dec 19, 06:00 PM EST',
        }))

    def test_accepts_ttp_labeled_beat_the_game_rooms(self):
        self.assertTrue(is_ttp_scheduled_room({
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_bot': 'Triforce Triple Play | Scheduled: Mon Dec 21, 08:00 PM EST',
        }))

    def test_rejects_unlabeled_beat_the_game_rooms(self):
        self.assertFalse(is_ttp_scheduled_room({
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_bot': 'Casual open room',
        }))


if __name__ == '__main__':
    unittest.main()
