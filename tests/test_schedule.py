import unittest
from datetime import date, datetime, time

from ttpbot.config import GOAL_NAME, POST_SEASON_GOAL_NAME, SEASON_END, TIMEZONE
from ttpbot.schedule import get_races_for_date, race_goal_for_time, race_info_for_time


class ScheduleTests(unittest.TestCase):
    def test_regular_season_ends_after_july_4_but_weekly_rooms_continue(self):
        self.assertEqual(SEASON_END, date(2026, 7, 4))

        final_day_races = get_races_for_date(date(2026, 7, 4))
        self.assertEqual(
            [race.time() for race in final_day_races],
            [time(0, 0), time(12, 0), time(15, 0), time(18, 0)],
        )

        monday_after_season = get_races_for_date(date(2026, 7, 6))
        self.assertEqual(
            [race.time() for race in monday_after_season],
            [time(20, 0), time(22, 0)],
        )

    def test_room_goal_and_info_change_after_regular_season(self):
        regular_season_race = datetime(2026, 7, 4, 18, 0, tzinfo=TIMEZONE)
        post_season_race = datetime(2026, 7, 6, 20, 0, tzinfo=TIMEZONE)

        self.assertEqual(race_goal_for_time(regular_season_race), GOAL_NAME)
        self.assertIn('TTP Season 4 | Scheduled:', race_info_for_time(regular_season_race))

        self.assertEqual(race_goal_for_time(post_season_race), POST_SEASON_GOAL_NAME)
        self.assertIn('Triforce Triple Play | Scheduled:', race_info_for_time(post_season_race))


if __name__ == '__main__':
    unittest.main()
