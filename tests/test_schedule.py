import unittest
from datetime import date, datetime, time
from unittest.mock import patch

from ttpbot.config import (
    GOAL_NAME,
    POST_SEASON_GOAL_NAME,
    RACE_NUMBER_MAP,
    SEASON_END,
    SEASON_START,
    TIMEZONE,
    WEEKLY_SCHEDULE,
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

        # Sunday's only slot is the 12:00 AM race closing out Saturday's
        # slate, and the Saturday before opening day is not in the season.
        sunday_before_season = get_races_for_date(date(2026, 8, 30))
        self.assertEqual(sunday_before_season, [])

        # Opening Monday has no 12:00 AM slot: Sunday has no evening slate,
        # so there is no Sunday slate for a midnight race to close out.
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

        # The final Saturday runs the 6 PM / 8 PM / 10 PM slate, after the
        # 12:00 AM race that closes out Friday.
        final_day_races = get_races_for_date(date(2026, 12, 19))
        self.assertEqual(
            [race.time() for race in final_day_races],
            [time(0, 0), time(18, 0), time(20, 0), time(22, 0)],
        )

        # Saturday's third race lands at 12:00 AM on the Sunday.
        final_slate_closer = get_races_for_date(date(2026, 12, 20))
        self.assertEqual(
            [race.time() for race in final_slate_closer],
            [time(0, 0)],
        )

    def test_saturday_adds_a_6_pm_race_to_the_weekday_evening_slate(self):
        saturday = get_races_for_date(date(2026, 9, 26))
        self.assertEqual(
            [race.time() for race in saturday],
            [time(0, 0), time(18, 0), time(20, 0), time(22, 0)],
        )

        # Sunday's only race is Saturday's 12:00 AM closer; no Sunday evening.
        sunday = get_races_for_date(date(2026, 9, 27))
        self.assertEqual([race.time() for race in sunday], [time(0, 0)])
        self.assertEqual(slate_date(date(2026, 9, 27), time(0, 0)), date(2026, 9, 26))

        # Monday still opens without a midnight race.
        monday = get_races_for_date(date(2026, 9, 28))
        self.assertEqual(
            [race.time() for race in monday],
            [time(20, 0), time(22, 0)],
        )

    def test_every_scheduled_time_has_an_announcement_number(self):
        # RACE_NUMBER_MAP is keyed by clock time alone, so a time is only safe
        # to use on a given day if it means the same race number everywhere.
        for weekday, times in WEEKLY_SCHEDULE.items():
            for slot in times:
                with self.subTest(weekday=weekday, slot=slot):
                    self.assertIn(slot, RACE_NUMBER_MAP)

        # 6 PM is TTP0, which only exists on Saturday. A weekday 6 PM race
        # would be announced as TTP0 too.
        days_with_6_pm = [
            weekday for weekday, times in WEEKLY_SCHEDULE.items()
            if time(18, 0) in times
        ]
        self.assertEqual(days_with_6_pm, [5])

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
        regular_season_race = datetime(2026, 12, 19, 22, 0, tzinfo=TIMEZONE)
        post_season_race = datetime(2026, 12, 21, 20, 0, tzinfo=TIMEZONE)

        self.assertEqual(GOAL_NAME, 'TTP Season 5')
        self.assertEqual(race_goal_for_time(regular_season_race), GOAL_NAME)
        self.assertIn('TTP Season 5 | Scheduled:', race_info_for_time(regular_season_race))

        self.assertEqual(race_goal_for_time(post_season_race), POST_SEASON_GOAL_NAME)
        self.assertIn('Triforce Triple Play | Scheduled:', race_info_for_time(post_season_race))

    def test_final_slate_midnight_race_is_still_regular_season(self):
        # The season ends Saturday, December 19, but that slate's third race
        # lands at 12:00 AM on Sunday, December 20. It belongs to the final
        # slate, so it must still use the TTP Season 5 goal.
        final_race = datetime(2026, 12, 20, 0, 0, tzinfo=TIMEZONE)
        self.assertEqual(race_goal_for_time(final_race), GOAL_NAME)
        self.assertIn('TTP Season 5 | Scheduled:', race_info_for_time(final_race))

        # The first post-season midnight race closes out Monday, December 21.
        first_post_season_midnight = datetime(2026, 12, 22, 0, 0, tzinfo=TIMEZONE)
        self.assertEqual(
            race_goal_for_time(first_post_season_midnight), POST_SEASON_GOAL_NAME
        )

        # The opening slate's midnight race was already regular season.
        opening_midnight = datetime(2026, 9, 1, 0, 0, tzinfo=TIMEZONE)
        self.assertEqual(race_goal_for_time(opening_midnight), GOAL_NAME)


if __name__ == '__main__':
    unittest.main()
