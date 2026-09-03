import unittest
from unittest.mock import patch

from ttpbot.bot import TTPBot
from ttpbot.config import GOAL_NAME, POST_SEASON_GOAL_NAME
from ttpbot.room_policy import is_league_room, is_ttp_scheduled_room

LEAGUE = {
    'goal': {'name': POST_SEASON_GOAL_NAME},
    'info_bot': 'League: SirLinkalot vs. Windfox470',
}
TTP_POST_SEASON = {
    'goal': {'name': POST_SEASON_GOAL_NAME},
    'info_bot': 'Triforce Triple Play | Scheduled: Mon Dec 21, 08:00 PM EST',
}
TTP_SEASON = {
    'goal': {'name': GOAL_NAME},
    'info_bot': 'TTP Season 5 | Scheduled: Mon Aug 31, 08:00 PM EDT',
}
COMMUNITY = {'goal': {'name': POST_SEASON_GOAL_NAME}, 'info_bot': 'Casual open room'}

# A seed roll by another authorised category bot (e.g. SahasrahBot)
# overwrites info_bot but never touches info_user.
LEAGUE_SEED_OVERWROTE_INFO_BOT = {
    'goal': {'name': POST_SEASON_GOAL_NAME},
    'info_bot': (
        'Seed: 4691165665847563006 - '
        'Flags: oIbnPfPb0mR7ggY12zwI0QNIY620UnhU8kiC3'
    ),
    'info_user': 'League: Droois vs. Rhinohero',
}


class LeagueRoomPolicyTests(unittest.TestCase):
    def test_recognises_a_league_room(self):
        self.assertTrue(is_league_room(LEAGUE))

    def test_recognised_after_a_seed_roll_overwrites_info_bot(self):
        # The regression this fix exists for: SahasrahBot rolled a seed in
        # the League room, overwriting info_bot, but info_user still holds
        # the League title.
        self.assertTrue(is_league_room(LEAGUE_SEED_OVERWROTE_INFO_BOT))

    def test_recognised_when_only_info_bot_carries_the_title(self):
        # Rooms opened before this fix only ever had the title in info_bot.
        self.assertTrue(is_league_room({
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_bot': 'League: SirLinkalot vs. Windfox470',
        }))

    def test_not_recognised_when_neither_field_carries_the_title(self):
        self.assertFalse(is_league_room({
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_bot': 'Seed: 123 - Flags: abc',
            'info_user': 'Some other note',
        }))

    def test_ttp_post_season_room_is_not_a_league_room(self):
        self.assertFalse(is_league_room(TTP_POST_SEASON))

    def test_league_room_is_not_a_ttp_room(self):
        self.assertFalse(is_ttp_scheduled_room(LEAGUE))

    def test_community_room_is_neither(self):
        self.assertFalse(is_league_room(COMMUNITY))
        self.assertFalse(is_ttp_scheduled_room(COMMUNITY))

    def test_league_room_requires_the_beat_the_game_goal(self):
        self.assertFalse(is_league_room({
            'goal': {'name': 'Something Else'},
            'info_bot': 'League: A vs. B',
        }))

    def test_missing_info_bot_is_not_a_league_room(self):
        self.assertFalse(is_league_room({'goal': {'name': POST_SEASON_GOAL_NAME}}))

    def test_none_info_bot_is_not_a_league_room(self):
        self.assertFalse(is_league_room({
            'goal': {'name': POST_SEASON_GOAL_NAME}, 'info_bot': None,
        }))

    def test_none_info_user_is_not_a_league_room(self):
        self.assertFalse(is_league_room({
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_bot': None,
            'info_user': None,
        }))

    def test_missing_info_bot_but_present_info_user_still_matches(self):
        self.assertTrue(is_league_room({
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_user': 'League: A vs. B',
        }))


class ShouldHandleTests(unittest.TestCase):
    def handle(self, race_data):
        bot = object.__new__(TTPBot)
        with patch('ttpbot.bot.Bot.should_handle', return_value=True):
            return bot.should_handle(race_data)

    def test_handles_league_rooms(self):
        self.assertTrue(self.handle(LEAGUE))

    def test_still_handles_ttp_rooms(self):
        self.assertTrue(self.handle(TTP_SEASON))
        self.assertTrue(self.handle(TTP_POST_SEASON))

    def test_still_ignores_community_rooms(self):
        self.assertFalse(self.handle(COMMUNITY))


if __name__ == '__main__':
    unittest.main()
