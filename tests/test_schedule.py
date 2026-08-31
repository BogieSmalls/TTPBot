import unittest
from datetime import date, datetime, time
from unittest.mock import patch

from ttpbot.config import (
    GOAL_NAME,
    POST_SEASON_GOAL_NAME,
    SEASON_END,
    SEASON_START,
    TIMEZONE,
)
from ttpbot.schedule import (
    get_races_for_date,
    race_goal_for_time,
    race_info_for_time,
    slate_date,
)


class ScheduleTests(unittest.TestCase):
    def test_ttp5_regular_season_window_and_weekly_schedule(self):
        self.assertEqual(SEASON_START, date(2026, 8, 31))
        self.assertEqual(SEASON_END, date(2026, 12, 19))

        sunday_before_season = get_races_for_date(date(2026, 8, 30))
        self.assertEqual(sunday_before_season, [])

        # Opening Monday has no 12:00 AM slot: Sunday runs no races, so there
        # is no Sunday slate for a midnight race to close out.
        opening_monday_races = get_races_for_date(date(2026, 8, 31))
        self.assertEqual(
            [race.time() for race in opening_monday_races],
            [time(20, 0), time(22, 0)],
        )

        # Tuesday keeps its midnight race, closing out the Monday slate.
        second_day_races = get_races_for_date(date(2026, 9, 1))
        self.assertEqual(
            [race.time() for race in second_day_races],
            [time(0, 0), time(20, 0), time(22, 0)],
        )

        final_day_races = get_races_for_date(date(2026, 12, 19))
        self.assertEqual(
            [race.time() for race in final_day_races],
            [time(0, 0), time(12, 0), time(15, 0), time(18, 0)],
        )

    def test_midnight_race_belongs_to_the_previous_evening_slate(self):
        # A 12:00 AM race is the third race of the prior evening's slate, so it
        # is suppressed when that evening falls before the season opens.
        self.assertEqual(slate_date(date(2026, 9, 1), time(0, 0)), date(2026, 8, 31))
        self.assertEqual(slate_date(date(2026, 9, 1), time(20, 0)), date(2026, 9, 1))

        with patch('ttpbot.schedule.SEASON_START', date(2026, 9, 1)):
            self.assertEqual(
                [race.time() for race in get_races_for_date(date(2026, 9, 1))],
                [time(20, 0), time(22, 0)],
            )

    def test_room_goal_and_info_change_after_ttp5_regular_season(self):
        regular_season_race = datetime(2026, 12, 19, 18, 0, tzinfo=TIMEZONE)
        post_season_race = datetime(2026, 12, 21, 20, 0, tzinfo=TIMEZONE)

        self.assertEqual(GOAL_NAME, 'TTP Season 5')
        self.assertEqual(race_goal_for_time(regular_season_race), GOAL_NAME)
        self.assertIn('TTP Season 5 | Scheduled:', race_info_for_time(regular_season_race))

        self.assertEqual(race_goal_for_time(post_season_race), POST_SEASON_GOAL_NAME)
        self.assertIn('Triforce Triple Play | Scheduled:', race_info_for_time(post_season_race))


if __name__ == '__main__':
    unittest.main()
