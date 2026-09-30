"""Inviting an Autumn room's racers, through the handler that holds its socket.

The invite itself can only be sent by the handler: `invite_user` writes to the
room's websocket, and nothing else has it. So the scheduler seeds a list and the
handler sends it, which is exactly how the League works.

The guard logic is shared between the two competitions rather than copied,
because it is subtle -- claimed before the sends so a concurrent `begin()` cannot
double-invite, released on failure so a dead socket does not strand everybody,
and kept in `self.state` rather than on the instance because racetime_bot
discards and rebuilds the handler around the same state dict.
"""

import asyncio
import logging
import unittest

from ttpbot.config import AUTUMN_ROOM_INFO_PREFIX, POST_SEASON_GOAL_NAME
from ttpbot.room_policy import is_autumn_room, is_league_room


class Log(logging.Logger):
    def __init__(self):
        super().__init__('handler')
        self.warnings = []
        self.infos = []

    def warning(self, msg, *args, **kw):
        self.warnings.append(msg % args if args else msg)

    def info(self, msg, *args, **kw):
        self.infos.append(msg % args if args else msg)


class Handler:
    """Just enough handler to exercise the invite path."""

    def __init__(self, state=None, entrants=(), data=None):
        from ttpbot.handler import TTPRaceHandler

        self.state = {} if state is None else state
        self.data = data or {'name': 'z1r/fancy-mario-1234', 'entrants': list(entrants)}
        self.logger = Log()
        self.invited = []
        self.fail_on_invite = False
        self._send_invites = TTPRaceHandler._send_invites.__get__(self, Handler)
        self._autumn_invite_ids = TTPRaceHandler._autumn_invite_ids.__get__(self, Handler)
        self._present_entrant_ids = TTPRaceHandler._present_entrant_ids.__get__(
            self, Handler)

    async def invite_user(self, racetime_id):
        if self.fail_on_invite:
            raise RuntimeError('websocket died')
        self.invited.append(racetime_id)


def run(coro):
    return asyncio.run(coro)


def autumn_room(info=None):
    return {
        'goal': {'name': POST_SEASON_GOAL_NAME},
        'info_user': info if info is not None else (
            AUTUMN_ROOM_INFO_PREFIX + ' — ISUMatt vs chessjerk [W1-1]'),
        'info_bot': '',
    }


class RecognisingTheRoom(unittest.TestCase):
    def test_an_autumn_room_is_recognised_by_its_prefix(self):
        self.assertTrue(is_autumn_room(autumn_room()))

    def test_and_is_not_a_league_room(self):
        # All three -- League, TTP post-season and the tournament -- share the
        # 'Beat the game' goal, so the prefix is what separates them. Neither must
        # be able to claim the other's rooms.
        room = autumn_room()
        self.assertFalse(is_league_room(room))
        league = {
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_user': 'League: Bogie vs. Merks', 'info_bot': '',
        }
        self.assertFalse(is_autumn_room(league))
        self.assertTrue(is_league_room(league))

    def test_a_community_room_is_neither(self):
        plain = {'goal': {'name': POST_SEASON_GOAL_NAME},
                 'info_user': 'friendly race', 'info_bot': ''}
        self.assertFalse(is_autumn_room(plain))
        self.assertFalse(is_league_room(plain))

    def test_another_goal_is_not_ours_whatever_the_info_says(self):
        self.assertFalse(is_autumn_room({
            'goal': {'name': 'Some other goal'},
            'info_user': AUTUMN_ROOM_INFO_PREFIX + ' — A vs B [W1-1]',
        }))

    def test_the_prefix_is_read_from_either_field(self):
        # SahasrahBot overwrites info_bot when it rolls a seed, so info_user is
        # preferred -- but a room whose info_bot still carries it counts.
        self.assertTrue(is_autumn_room({
            'goal': {'name': POST_SEASON_GOAL_NAME},
            'info_user': 'a seed link',
            'info_bot': AUTUMN_ROOM_INFO_PREFIX + ' — A vs B [W1-1]',
        }))


class ReadingTheSeededList(unittest.TestCase):
    def test_two_distinct_ids_are_taken(self):
        it = Handler(state={'autumn_race': {'invite': ['aaa', 'bbb']}})
        self.assertEqual(it._autumn_invite_ids(), ['aaa', 'bbb'])

    def test_an_empty_list_is_not_a_complaint(self):
        # The scheduler seeds an empty list on purpose when a racer has no
        # racetime id, and it already said so itself.
        it = Handler(state={'autumn_race': {'invite': []}})
        self.assertEqual(it._autumn_invite_ids(), [])
        self.assertEqual(it.logger.warnings, [])

    def test_anything_else_is_reported(self):
        # One id, or a repeated one, means somebody's id is missing or wrong, and
        # inviting half a match is worse than inviting none of it.
        for bad in (['aaa'], ['aaa', 'aaa'], ['aaa', 'bbb', 'ccc'], ['aaa', None]):
            with self.subTest(bad=bad):
                it = Handler(state={'autumn_race': {'invite': bad}})
                self.assertEqual(it._autumn_invite_ids(), [])
                self.assertTrue(it.logger.warnings, bad)

    def test_nothing_seeded_is_nothing_to_do(self):
        # No title fallback, unlike the League's: the ids live in the bracket
        # engine, and reaching for them here would put an HTTP call inside a
        # websocket handler. The scheduler re-seeds every tick instead.
        it = Handler(state={})
        self.assertEqual(it._autumn_invite_ids(), [])
        self.assertEqual(it.logger.warnings, [])


class SendingThem(unittest.TestCase):
    def test_both_racers_are_invited_once(self):
        it = Handler(state={'autumn_race': {'invite': ['aaa', 'bbb']}})
        run(it._send_invites('Autumn', 'autumn_invited', it._autumn_invite_ids()))
        self.assertEqual(it.invited, ['aaa', 'bbb'])
        self.assertTrue(it.state['autumn_invited'])

        # A rebuilt handler around the same state sends nothing.
        again = Handler(state=it.state)
        run(again._send_invites(
            'Autumn', 'autumn_invited', again._autumn_invite_ids()))
        self.assertEqual(again.invited, [])

    def test_somebody_already_in_the_room_is_not_invited_again(self):
        it = Handler(
            state={'autumn_race': {'invite': ['aaa', 'bbb']}},
            entrants=[{'user': {'id': 'aaa'}}],
        )
        run(it._send_invites('Autumn', 'autumn_invited', it._autumn_invite_ids()))
        self.assertEqual(it.invited, ['bbb'])

    def test_everybody_already_in_claims_the_guard_without_sending(self):
        it = Handler(
            state={'autumn_race': {'invite': ['aaa', 'bbb']}},
            entrants=[{'user': {'id': 'aaa'}}, {'user': {'id': 'bbb'}}],
        )
        run(it._send_invites('Autumn', 'autumn_invited', it._autumn_invite_ids()))
        self.assertEqual(it.invited, [])
        self.assertTrue(it.state['autumn_invited'])

    def test_a_dead_socket_releases_the_guard(self):
        # Claimed before the sends so a concurrent begin() cannot double-invite,
        # which means a failure has to give it back or every rebuilt handler skips
        # and nobody is ever invited.
        it = Handler(state={'autumn_race': {'invite': ['aaa', 'bbb']}})
        it.fail_on_invite = True
        with self.assertRaises(RuntimeError):
            run(it._send_invites(
                'Autumn', 'autumn_invited', it._autumn_invite_ids()))
        self.assertFalse(it.state['autumn_invited'])
        self.assertTrue(any('Autumn invites failed' in w for w in it.logger.warnings))

    def test_the_guard_is_the_competitions_own(self):
        # An Autumn failure must not release the League's guard, and vice versa.
        # The shared implementation takes the key, and this is what says so.
        it = Handler(state={
            'autumn_race': {'invite': ['aaa', 'bbb']},
            'league_invited': True,
        })
        it.fail_on_invite = True
        with self.assertRaises(RuntimeError):
            run(it._send_invites(
                'Autumn', 'autumn_invited', it._autumn_invite_ids()))
        self.assertTrue(it.state['league_invited'], 'the League guard is untouched')
        self.assertFalse(it.state['autumn_invited'])

    def test_the_log_names_the_competition(self):
        it = Handler(state={'autumn_race': {'invite': ['aaa', 'bbb']}})
        run(it._send_invites('Autumn', 'autumn_invited', it._autumn_invite_ids()))
        self.assertTrue(any('2 Autumn racers' in i for i in it.logger.infos),
                        it.logger.infos)


if __name__ == '__main__':
    unittest.main()
