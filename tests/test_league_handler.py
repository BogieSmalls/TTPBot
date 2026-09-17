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

    async def test_does_not_reinvite_when_a_reconnect_recreates_the_handler(self):
        # racetime_bot deletes the handler when its websocket task ends and
        # builds a brand-new one for the same race on reconnect, but
        # self.state is the SAME dict object across that recreation. The
        # once-only guard must live in that shared state, not on the
        # instance, or the second handler invites both racers again.
        shared_state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}

        first = make_handler(shared_state)
        await first.begin()

        second = make_handler(shared_state)
        await second.begin()

        first.invite_user.assert_awaited()
        second.invite_user.assert_not_awaited()

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

    async def test_recovers_invites_from_info_user_after_a_seed_roll(self):
        # SahasrahBot rolled a seed and overwrote info_bot; info_user still
        # holds the League title this automation wrote at room creation.
        handler = make_handler({})
        handler.data = dict(
            LEAGUE_DATA,
            info_bot=(
                'Seed: 4691165665847563006 - '
                'Flags: oIbnPfPb0mR7ggY12zwI0QNIY620UnhU8kiC3'
            ),
            info_user='League: SirLinkalot vs. Windfox470',
        )

        await handler.begin()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['vrZyM4orOEWqDJX0', 'd17DexWEMqWak64R'],
        )

    async def test_still_recovers_invites_from_info_bot_when_info_user_absent(self):
        # Rooms opened before this fix only ever had the title in info_bot.
        handler = make_handler({})
        handler.data = dict(LEAGUE_DATA)
        handler.data.pop('info_user', None)

        await handler.begin()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['vrZyM4orOEWqDJX0', 'd17DexWEMqWak64R'],
        )

    async def test_invites_nobody_when_neither_field_carries_the_title(self):
        handler = make_handler({})
        handler.data = dict(
            LEAGUE_DATA,
            info_bot='Seed: 123 - Flags: abc',
            info_user='Some other note',
        )

        await handler.begin()

        handler.invite_user.assert_not_awaited()

    async def test_falls_through_to_title_when_seeded_invite_has_one_entry(self):
        handler = make_handler({'league_race': {'invite': ['rt-sir']}})

        await handler.begin()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['vrZyM4orOEWqDJX0', 'd17DexWEMqWak64R'],
        )

    async def test_falls_through_to_title_when_seeded_invite_has_three_entries(self):
        handler = make_handler(
            {'league_race': {'invite': ['rt-sir', 'rt-wind', 'rt-extra']}}
        )

        await handler.begin()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['vrZyM4orOEWqDJX0', 'd17DexWEMqWak64R'],
        )

    async def test_falls_through_to_title_when_seeded_invite_is_not_a_list(self):
        handler = make_handler({'league_race': {'invite': 'rt-sir,rt-wind'}})

        await handler.begin()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['vrZyM4orOEWqDJX0', 'd17DexWEMqWak64R'],
        )

    async def test_falls_through_to_title_when_seeded_invite_has_a_non_string(self):
        handler = make_handler({'league_race': {'invite': ['rt-sir', 42]}})

        await handler.begin()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['vrZyM4orOEWqDJX0', 'd17DexWEMqWak64R'],
        )

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


class LeagueInviteRetryTests(unittest.IsolatedAsyncioTestCase):
    """A half-sent invite must not strand the once-only guard.

    The guard is claimed before the sends so a concurrent begin() cannot
    double-invite. That is right, but it means a websocket dying mid-send
    would leave it set with nobody invited, and every handler rebuilt by
    refresh_races would skip straight past.
    """

    async def test_releases_the_guard_when_a_send_fails(self):
        state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}
        handler = make_handler(state)
        handler.invite_user = AsyncMock(side_effect=OSError('socket died'))

        with self.assertRaises(OSError):
            await handler._send_league_invites()

        # Released, so the next handler retries rather than assuming done.
        self.assertFalse(state.get('league_invited'))

    async def test_a_retry_after_a_failure_invites_both(self):
        state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}
        handler = make_handler(state)
        handler.invite_user = AsyncMock(side_effect=OSError('socket died'))

        with self.assertRaises(OSError):
            await handler._send_league_invites()

        # The rebuilt handler shares the same state dict.
        retry = make_handler(state)
        await retry._send_league_invites()

        self.assertEqual(
            [c.args[0] for c in retry.invite_user.await_args_list],
            ['rt-sir', 'rt-wind'],
        )

    async def test_a_completed_invite_still_only_happens_once(self):
        state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}
        handler = make_handler(state)

        await handler._send_league_invites()
        await make_handler(state)._send_league_invites()

        # The release must not cost the double-invite protection.
        self.assertEqual(handler.invite_user.await_count, 2)
        self.assertTrue(state.get('league_invited'))


def make_handler_with_entrants(state, entrant_ids):
    handler = make_handler(state)
    handler.data = dict(
        LEAGUE_DATA,
        entrants=[{'user': {'id': i}} for i in entrant_ids],
    )
    return handler


class LeagueInviteSkipsExistingEntrantsTests(unittest.IsolatedAsyncioTestCase):
    """invite_user() only writes to the socket - it never learns the answer.

    So a duplicate invitation is not something the bot would find out about,
    and a retry after a half-sent batch, or after a restart, must not re-send
    for somebody already in the room.
    """

    async def test_retries_only_the_racer_who_is_still_missing(self):
        state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}
        # The first send landed, the second died with the socket.
        failing = make_handler(state)
        failing.invite_user = AsyncMock(side_effect=[None, OSError('socket died')])
        with self.assertRaises(OSError):
            await failing._send_league_invites()

        retry = make_handler_with_entrants(state, ['rt-sir'])
        await retry._send_league_invites()

        self.assertEqual(
            [c.args[0] for c in retry.invite_user.await_args_list], ['rt-wind'],
        )

    async def test_a_restart_with_both_present_invites_nobody(self):
        # No seeded state, as after a restart: _league_invite_ids falls back to
        # the room title and resolves the real roster ids, so the entrants have
        # to be those same ids for this to mean anything.
        handler = make_handler_with_entrants(
            {}, ['vrZyM4orOEWqDJX0', 'd17DexWEMqWak64R'])

        await handler._send_league_invites()

        # State is gone after a restart, so the title fallback resolves both
        # again - but they are already entrants and need nothing.
        handler.invite_user.assert_not_awaited()

    async def test_marks_the_work_done_when_everyone_is_already_in(self):
        state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}
        handler = make_handler_with_entrants(state, ['rt-sir', 'rt-wind'])

        await handler._send_league_invites()

        # Nothing left to do, so later handlers should not re-check.
        self.assertTrue(state.get('league_invited'))

    async def test_invites_everyone_when_the_room_is_empty(self):
        state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}
        handler = make_handler_with_entrants(state, [])

        await handler._send_league_invites()

        self.assertEqual(
            [c.args[0] for c in handler.invite_user.await_args_list],
            ['rt-sir', 'rt-wind'],
        )

    async def test_tolerates_a_room_payload_without_entrants(self):
        state = {'league_race': {'invite': ['rt-sir', 'rt-wind']}}
        handler = make_handler(state)

        await handler._send_league_invites()

        self.assertEqual(handler.invite_user.await_count, 2)


from ttpbot.league.roster import load_roster


class CoopInviteTests(unittest.IsolatedAsyncioTestCase):
    async def test_invites_four_seeded_runners(self):
        handler = make_handler({'league_race': {'invite': ['rt-a', 'rt-b', 'rt-c', 'rt-d']}})

        await handler.begin()

        self.assertEqual([c.args[0] for c in handler.invite_user.await_args_list],
                         ['rt-a', 'rt-b', 'rt-c', 'rt-d'])

    async def test_falls_through_to_title_when_seeded_ids_repeat(self):
        handler = make_handler({'league_race': {'invite': ['rt-sir', 'rt-sir']}})

        await handler.begin()

        invited = [c.args[0] for c in handler.invite_user.await_args_list]
        self.assertEqual(len(invited), 2)
        self.assertEqual(len(set(invited)), 2)

    async def test_recovers_four_invites_from_a_coop_title_after_a_restart(self):
        handler = make_handler({})
        handler.data = dict(
            LEAGUE_DATA, info_bot='',
            info_user='League: Windfox470 & seanfreston vs. SirLinkalot & Stags28',
        )
        roster = load_roster()
        expected = [roster.resolve(name).racetime_id
                    for name in ('Windfox470', 'seanfreston', 'SirLinkalot', 'Stags28')]

        await handler.begin()

        self.assertEqual([c.args[0] for c in handler.invite_user.await_args_list], expected)

    async def test_invites_nobody_when_the_sides_are_uneven(self):
        handler = make_handler({})
        handler.data = dict(LEAGUE_DATA, info_bot='League: Windfox470 & seanfreston vs. SirLinkalot')

        await handler.begin()

        handler.invite_user.assert_not_awaited()
