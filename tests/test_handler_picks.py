"""Picks, roll checks and flagset recording in a TTP tournament room.

Driven through the handler as racetime drives it: race data, chat, and chat
history after a restart. The engine is a stand-in that keeps picks.mjs's rules;
one test at the end runs the client against the real engine.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ttpbot import tournament_room as tr
from ttpbot.autumn.engine import (AutumnEngine, EngineUnreachable, NOT_RECORDED, RECORDED,
                                  UNCONFIRMED, Written)
from ttpbot.autumn.results import AutumnResults
from ttpbot.handler import TTPRaceHandler
from ttpbot.provider import RacetimeProvider
from ttpbot.state import DestinationStateStore

LOG = logging.getLogger('ttp-picks-test')
START = datetime(2027, 1, 9, 1, 0, tzinfo=timezone.utc)
TITLE = 'TTP Season 5 Tournament — Round 1 — thomjay vs specialk3782 [W1-25] Game {}'
ROOM_URL = 'https://racetime.gg/z1r/ttp-room'
UPHILL = '143oNtDD4Pw3Yw6SRryTCXlqTsRz2fxg4s4pJW'
MUFFLE = '2487lt0moGMFLRug1ofQdxPuUBWnJVSsi7d202I'
PICK5 = 'p8GIOeYEAonHy5131VdG6b94Ts3EMMvBaQoWR'
FLAGSETS = [
    {'name': 'TTP5 Uphill Battle', 'flags': UPHILL, 'command': 'ttp5uphill'},
    {'name': 'TTP5 Muffle Rug', 'flags': MUFFLE, 'command': 'ttp5muffle'},
    {'name': 'TTP5 Pick 5', 'flags': PICK5, 'command': 'ttp5pick5'},
]
IDS = {'thomjay': 'id-thomjay', 'specialk3782': 'id-specialk', 'host': 'id-host'}
ROLLED = 'Seed rolling complete.  See race info for details.'
RESET = 'Reseting bot state.  You may now roll a new game.'
CLEARED = 'Seed cleared. You may now roll a new one.'


class Refused(Exception):
    pass


class Engine:
    """picks.mjs's rules for one series: thomjay (seed 25) vs specialk3782 (seed 40)."""

    def __init__(self, decision=None, games=None, chosen=None, score=(0, 0)):
        self.better, self.other = 'thomjay', 'specialk3782'
        self.decision = {'choice': decision, 'by': 'racer', 'actor': self.better} if decision else None
        self.games = {n: {'flagset': name, 'flags': self.flags_of(name), 'source': 'room-start',
                          'room': 'https://racetime.gg/z1r/game-{}'.format(n), 'actor': 'TTPBot'}
                      for n, name in (games or {}).items()}
        self.chosen = {n: {'flagset': name, 'by': 'racer', 'actor': 'x'} for n, name in (chosen or {}).items()}
        self.score = score
        self.flagsets = list(FLAGSETS)
        self.down = False
        self.recorded = []
        self.writes = []
        #: Room warnings kept for the series card, and whether they are kept.
        self.notes = []
        self.noting = True

    @staticmethod
    def flags_of(name):
        return next(f['flags'] for f in FLAGSETS if f['name'] == name)

    def pick_state(self):
        owners = {'1': None, '2': None}
        if self.decision:
            pick = self.decision['choice'] == 'pick'
            owners = {'1': self.better if pick else self.other, '2': self.other if pick else self.better}
        played = [self.games[n]['flagset'] for n in sorted(self.games)]
        left = [f['name'] for f in self.flagsets if f['name'] not in played]
        return {'matchId': 'W1-25', 'betterSeed': self.better, 'otherSeed': self.other,
                'decision': self.decision, 'owners': owners,
                'chosen': {str(n): dict(c) for n, c in self.chosen.items()},
                'games': {str(n): dict(g) for n, g in self.games.items()}, 'played': played, 'left': left,
                'game3': left[0] if len(played) >= 2 and len(left) == 1 else None,
                'warnings': ['A flagset was played twice, so Council decides Game 3.']
                if len(set(played)) < len(played) else []}

    def _reachable(self):
        if self.down:
            raise EngineUnreachable('engine unreachable at /tournament/picks: refused')

    async def picks(self, match_id, edition=None):
        self._reachable()
        return self.pick_state()

    async def tournament_flagsets(self):
        self._reachable()
        return list(self.flagsets)

    async def draw(self):
        self._reachable()
        return {'matches': [{'id': 'W1-25', 'a': self.better, 'b': self.other,
                             'series': {'bestOf': 3, 'a': self.score[0], 'b': self.score[1], 'decided': False}}]}

    async def _write(self, op, payload, change):
        self.writes.append((op, payload))
        if self.down:
            return Written(UNCONFIRMED, detail='engine unreachable')
        try:
            change(payload)
        except Refused as refusal:
            return Written(NOT_RECORDED, detail=json.dumps({'error': str(refusal)}))
        return Written(RECORDED, answer={"picks": self.pick_state()})

    async def decide_pick(self, payload):
        def change(p):
            if p['actor'] != self.better:
                raise Refused('only the better seed, {}, decides who picks first'.format(self.better))
            if self.decision:
                raise Refused('W1-25 already has a decision; Council can change it')
            self.decision = {'choice': p['choice'], 'by': p['by'], 'actor': p['actor']}
        return await self._write('decidePick', payload, change)

    async def choose_pick(self, payload):
        def change(p):
            owner = self.pick_state()["owners"][str(p['game'])]
            if p['actor'] != owner:
                raise Refused("This one is {}'s pick.".format(owner))
            self.chosen[p['game']] = {'flagset': p['flagset'], 'by': p['by'], 'actor': p['actor']}
        return await self._write('choosePick', payload, change)

    async def record_flagset(self, payload):
        def change(p):
            found = next((f for f in self.flagsets if f['flags'] == p['flags']), None)
            if not found:
                raise Refused("that flag string is not on the tournament's frozen list")
            was = self.games.get(p['game'])
            if was and was['room'] == p['room']:
                if was['flags'] != found['flags']:
                    raise Refused('already recorded from this room')
                return
            self.games[p['game']] = {'flagset': found['name'], 'flags': found['flags'], 'source': p['source'],
                                     'room': p['room'], 'actor': p['actor']}
            self.recorded.append((p['matchId'], p['game'], p['flags'], p['source']))
        return await self._write('recordFlagset', payload, change)

    async def note_warning(self, payload):
        if self.down or not self.noting:
            return Written(UNCONFIRMED, detail='engine unreachable')
        self.notes.append(dict(payload))
        return Written(RECORDED, answer={'picks': self.pick_state()})


class LiveOps:
    def __init__(self, accepts=True):
        self.accepts = accepts
        self.posts = []

    async def __call__(self, text):
        self.posts.append(text)
        return self.accepts

    def told(self, words):
        return any(words in post for post in self.posts)


class Room:
    """One race room as racetime shows it to TTPBot's handler."""

    def __init__(self, game=1, engine=None, live_ops=None):
        self.game = game
        self.engine = engine if engine is not None else Engine()
        self.live_ops = live_ops if live_ops is not None else LiveOps()
        self.chat = []
        self.clock = datetime(2027, 1, 9, 0, 30, tzinfo=timezone.utc)
        self.data = {'name': 'z1r/ttp-room', 'url': '/z1r/ttp-room', 'goal': {'name': 'Beat the game'},
                     'info_user': TITLE.format(game), 'info_bot': '', 'status': {'value': 'open'},
                     'entrants': []}
        self.handler = None
        self.said = []

    @property
    def info_bot(self):
        return self.data['info_bot']

    def _stamp(self):
        self.clock += timedelta(seconds=1)
        return self.clock.isoformat().replace('+00:00', 'Z')

    def seed(self, state):
        state['autumn_race'] = {
            'invite': [IDS['thomjay'], IDS['specialk3782']], 'title': TITLE.format(self.game), 'event': 'ttp',
            'edition': '5', 'match_id': 'W1-25', 'game': self.game, 'runners': ['thomjay', 'specialk3782'],
            'when': START.isoformat(), 'round': 'Round 1'}

    async def open(self, seeded=True):
        """Connect: begin, then the chat history racetime sends back."""
        state = {}
        handler = TTPRaceHandler(conn=None, logger=LOG, state=state)
        handler.data = dict(self.data)
        handler.ws = SimpleNamespace(send=AsyncMock())
        handler.invite_user = AsyncMock()
        handler.ttp_engine = self.engine
        handler.ttp_live_ops = self.live_ops
        handler.ttp_room_url = lambda name: 'https://racetime.gg/' + name
        handler._ttp_loop = AsyncMock()

        async def send_message(text, **kwargs):
            self.said.append(text)
            self.chat.append({'is_bot': True, 'bot': 'TTPBot', 'message_plain': text, 'message': text,
                              'posted_at': self._stamp()})

        async def set_bot_raceinfo(info):
            self.data['info_bot'] = info
        handler.send_message = send_message
        handler.set_bot_raceinfo = set_bot_raceinfo
        self.handler = handler
        if seeded:
            self.seed(state)
        # TTP is switched on: with Z1RR_TTP_ENABLED off no room is a tournament room.
        with patch.dict('os.environ', {'Z1RR_TTP_ENABLED': 'true'}):
            await handler.begin()
        await handler.chat_history({'messages': list(self.chat)})
        return handler

    async def land_seed(self):
        """The runner's tick, after the seed lands."""
        self.seed(self.handler.state)
        await self.handler.state['_autumn_send_invites']()

    async def restart(self):
        """A process restart: a new handler, new state, and the room's history."""
        self.said.clear()
        return await self.open()

    async def race_data(self):
        await self.handler.race_data({'race': dict(self.data)})

    async def bot_says(self, bot, text):
        message = {'is_bot': True, 'bot': bot, 'user': None, 'message_plain': text, 'message': text,
                   'posted_at': self._stamp()}
        self.chat.append(message)
        await self.handler.chat_message({'message': message})

    async def roll(self, bot, info):
        self.data['info_bot'] = info
        await self.race_data()
        await self.bot_says(bot, ROLLED)

    async def command(self, text, who='host'):
        message = {'is_bot': False, 'is_system': False, 'message': text, 'message_plain': text,
                   'user': {'id': IDS.get(who, 'id-' + who), 'name': who}, 'posted_at': self._stamp()}
        self.chat.append(message)
        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await self.handler.chat_message({'message': message})

    async def start(self):
        self.data['status'] = {'value': 'in_progress'}
        await self.race_data()

    def council_said(self, base):
        return tr.with_council(base, True)


def history(*entries):
    """Chat history: (bot, text) for bot lines, (person, text, True) for people."""
    stamp = datetime(2027, 1, 9, 0, 31, tzinfo=timezone.utc)
    messages = []
    for entry in entries:
        stamp += timedelta(seconds=5)
        who, text = entry[0], entry[1]
        if len(entry) == 3:
            messages.append({'is_bot': False, 'message': text, 'message_plain': text,
                             'user': {'id': IDS.get(who, 'id-' + who), 'name': who},
                             'posted_at': stamp.isoformat().replace('+00:00', 'Z')})
        else:
            messages.append({'is_bot': True, 'bot': who, 'user': None, 'message': text, 'message_plain': text,
                             'posted_at': stamp.isoformat().replace('+00:00', 'Z')})
    return messages


def game3_engine():
    return Engine('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}, score=(1, 1))


class CancelledRolls(unittest.IsolatedAsyncioTestCase):
    async def test_a_cancelled_roll_uses_nothing_up(self):  # Codex requirement 1, using the formats in the room logs
        room = Room(3, game3_engine())
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)         # warned as wrong
        wrong = room.council_said('That seed is Uphill Battle, which was already played. This game should be '
                                  'Pick 5. !cancel clears it')
        self.assertIn(wrong, room.said)
        await room.bot_says('SahasrahBot', RESET)
        await room.roll('SahasrahBot', info='Seed: 2 - Flags: {} - Hash: A B C D'.format(PICK5))
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 3, PICK5, 'room-start')])
        self.assertEqual(room.said.count(wrong), 1)

    async def test_a_repeated_wrong_roll_after_a_cancel_is_warned_again(self):
        room = Room(3, game3_engine())
        await room.open()
        wrong = room.council_said('That seed is Uphill Battle, which was already played. This game should be '
                                  'Pick 5. !cancel clears it')
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        await room.bot_says('SahasrahBot', RESET)
        self.assertEqual(room.said[-1], tr.CANCELLED_WARNING)
        await room.roll('SahasrahBot', info='Seed: 2 - Flags: ' + UPHILL)
        self.assertEqual(room.said.count(wrong), 2)
        self.assertEqual(sum('That seed is Uphill Battle' in post for post in room.live_ops.posts), 2)
        # Once per roll: the same roll seen again says nothing more.
        await room.race_data()
        await room.restart()
        self.assertEqual(room.said, [])
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 3, UPHILL, 'room-start')])

    async def test_ttpbot_s_own_roll_is_not_checked_against_stale_race_info(self):
        room = Room(3, game3_engine())
        await room.open()
        wrong = room.council_said('That seed is Uphill Battle, which was already played. This game should be '
                                  'Pick 5. !cancel clears it')
        await room.command('!flags ' + UPHILL)              # TTPBot rolls; racetime pushes the info after
        await room.race_data()
        self.assertEqual(room.said.count(wrong), 1)
        await room.command('!cancel')
        self.assertEqual(room.said[-1], tr.CANCELLED_WARNING)
        await room.command('!flags ' + PICK5)               # race info still shows Uphill here
        self.assertEqual(room.said.count(wrong), 1)
        await room.race_data()
        self.assertEqual(room.said.count(wrong), 1)
        self.assertEqual(len(room.live_ops.posts), 1)
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 3, PICK5, 'room-start')])

    async def test_a_restart_compares_only_against_what_was_said_since_the_last_roll(self):
        room = Room(3, game3_engine())
        wrong = room.council_said('That seed is Uphill Battle, which was already played. This game should be '
                                  'Pick 5. !cancel clears it')
        room.chat = history(('SahasrahBot', ROLLED), ('TTPBot', wrong), ('SahasrahBot', RESET),
                            ('TTPBot', tr.CANCELLED_WARNING), ('SahasrahBot', ROLLED))
        room.data['info_bot'] = 'Seed: 2 - Flags: ' + UPHILL
        await room.restart()
        self.assertEqual(room.said.count(wrong), 1)

    async def test_a_cancel_after_a_ttpbot_handoff_with_no_reroll_records_nothing(self):  # Review Focus 1
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + PICK5)          # an allowed flagset
        await room.command('!ttpbot')
        self.assertTrue(room.handler.sahasrahbot_overridden)
        await room.command('!cancel')                                             # TTPBot: "Seed cleared."
        self.assertIn(CLEARED, room.said)
        await room.start()
        self.assertIn('Flags: ' + PICK5, room.info_bot)
        self.assertEqual(room.engine.recorded, [])
        self.assertTrue(room.live_ops.told('no roll standing'))
        self.assertIn(room.council_said(tr.NO_ROLL_AT_START), room.said)

    async def test_a_cancel_found_in_chat_history_still_counts_after_a_restart(self):
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        room.chat = history(('SahasrahBot', 'Seed: 1 - Flags: ' + PICK5), ('SahasrahBot', ROLLED),
                            ('host', '!cancel', True), ('SahasrahBot', RESET))
        room.data['info_bot'] = 'Seed: 1 - Flags: ' + PICK5
        handler = await room.restart()
        self.assertEqual(handler.roll_state, 'cancelled')
        self.assertFalse(handler.seed_rolled)
        await room.start()
        self.assertEqual(room.engine.recorded, [])
        self.assertTrue(room.live_ops.told('no roll standing'))

    async def test_a_roll_after_the_cancel_stands_again(self):
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        room.chat = history(('SahasrahBot', ROLLED), ('SahasrahBot', RESET), ('SahasrahBot', ROLLED))
        room.data['info_bot'] = 'Seed: 2 - Flags: ' + PICK5
        handler = await room.restart()
        self.assertEqual(handler.roll_state, 'rolled')
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 2, PICK5, 'room-start')])

    async def test_a_restart_after_a_cancel_lets_ttpbot_roll_again(self):  # seed_rolled follows roll_state
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        room.chat = history(('SahasrahBot', 'Hi!'), ('SahasrahBot', ROLLED), ('host', '!ttpbot', True),
                            ('TTPBot', CLEARED))
        room.data['info_bot'] = 'Seed: 1 - Flags: ' + UPHILL
        handler = await room.restart()
        self.assertTrue(handler.sahasrahbot_overridden)
        self.assertFalse(handler.seed_rolled)
        await room.command('!flags ' + PICK5, who='host')
        self.assertIn('Seed rolling complete.  See race info for details.', room.said)
        self.assertEqual(handler.roll_state, 'rolled')
        await room.race_data()
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 2, PICK5, 'room-start')])

    async def test_a_person_typing_seed_cleared_changes_nothing(self):
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + PICK5)
        await room.command(CLEARED, who='specialk3782')
        await room.command(RESET, who='thomjay')
        self.assertEqual(room.handler.roll_state, 'rolled')
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 2, PICK5, 'room-start')])

    async def test_history_with_no_roll_records_nothing_rather_than_guess(self):
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        # The history window no longer reaches back to the roll.
        room.chat = history(('thomjay', 'glhf', True), ('specialk3782', 'you too', True))
        room.data['info_bot'] = 'Seed: 1 - Flags: ' + PICK5
        handler = await room.restart()
        self.assertEqual(handler.roll_state, 'none')
        await room.start()
        self.assertEqual(room.engine.recorded, [])
        self.assertTrue(room.live_ops.told('no roll standing'))

    async def test_race_info_blank_or_unchanged_after_a_cancel_warns_until_a_new_roll(self):  # staging shows which is real
        for after_cancel in ('Seed: 1 - Flags: ' + PICK5, ''):
            with self.subTest(info=after_cancel):
                room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
                await room.open()
                await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + PICK5)
                room.data['info_bot'] = after_cancel
                await room.race_data()
                await room.bot_says('SahasrahBot', RESET)
                self.assertEqual(room.said[-1], tr.CANCELLED_WARNING)
                await room.race_data()
                self.assertEqual(room.said[-1], tr.CANCELLED_WARNING)
                await room.start()
                self.assertEqual(room.engine.recorded, [])
                self.assertEqual(room.said[-1], room.council_said(tr.NO_ROLL_AT_START))

    async def test_two_flags_in_info_uses_the_last(self):
        room = Room(3, game3_engine())
        await room.open()
        await room.roll('SahasrahBot',
                        info='TTP Season 5 | Flags: {} Seed: 1 | Seed: 2 - Flags: {}'.format(UPHILL, PICK5))
        self.assertFalse(room.live_ops.posts)
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 3, PICK5, 'room-start')])

    async def test_no_roll_at_the_start_records_nothing_and_warns(self):
        room = Room(3, game3_engine())
        await room.open()
        await room.start()
        self.assertEqual(room.engine.recorded, [])
        self.assertEqual(room.said[-1], room.council_said(tr.NO_ROLL_AT_START))
        self.assertTrue(room.live_ops.told('TTP5 W1-25 Game 3 (https://racetime.gg/z1r/ttp-room): '
                                           'The race started with no roll standing'))


class Finished(unittest.IsolatedAsyncioTestCase):
    def results(self, engine, log=None, error=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        provider = RacetimeProvider('https://racetime.gg', 'z1r')
        store = DestinationStateStore('ttp_results.json', provider.destination_key, 'autumn_results',
                                      data_dir=temp.name)
        engine.state = AsyncMock(return_value={'document': {'racetimeIds': dict(
            thomjay=IDS['thomjay'], specialk3782=IDS['specialk3782']), 'results': {}, 'games': {}}})
        reader = AsyncMock(side_effect=error) if error else AsyncMock(return_value=log)
        live_ops = LiveOps()
        results = AutumnResults(store=store, engine=engine, provider=provider, logger=LOG, event='ttp', edition='5',
                                notify=live_ops, log_reader=reader)
        race = SimpleNamespace(match_id='W1-25', runner_one='thomjay', runner_two='specialk3782',
                               identity=SimpleNamespace(game=2))
        results.bind(race, ROOM_URL, {'thomjay': IDS['thomjay'], 'specialk3782': IDS['specialk3782']})
        return results, live_ops, reader

    def finished(self, info):
        return {'name': 'z1r/ttp-room', 'status': {'value': 'finished'}, 'info_bot': info, 'entrants': [
            {'user': {'id': IDS['thomjay']}, 'status': {'value': 'done'}, 'finish_time': 'PT1H0M0S'},
            {'user': {'id': IDS['specialk3782']}, 'status': {'value': 'done'}, 'finish_time': 'PT1H1M0S'}]}

    LOG_ROLLED = '\n'.join([
        '[2027-01-09 00:51:00+00:00] SahasrahBot updated the race information.',
        '[2027-01-09 00:51:00+00:00] SahasrahBot: Seed: 9 - Flags: ' + PICK5,
        '[2027-01-09 00:51:00+00:00] SahasrahBot: ' + ROLLED,
    ])

    async def test_a_flagset_missed_at_the_start_is_recovered_from_the_finished_room(self):  # Review Focus 3
        engine = Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0))
        room = Room(2, engine)
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 9 - Flags: ' + PICK5)
        engine.down = True
        await room.start()                                  # missed: the engine is down
        self.assertEqual(engine.recorded, [])
        self.assertTrue(room.live_ops.told("couldn't confirm this game's flagset"))
        self.assertTrue(room.handler.data['status']['value'] == 'in_progress')
        engine.down = False
        engine.draw = AsyncMock(return_value={'matches': [{'id': 'W1-25', 'a': 'thomjay', 'b': 'specialk3782'}]})
        results, live_ops, reader = self.results(engine, log=self.LOG_ROLLED)
        await results.record(self.finished('Seed: 9 - Flags: ' + PICK5))
        reader.assert_awaited_once_with(ROOM_URL)
        self.assertEqual(engine.recorded, [('W1-25', 2, PICK5, 'room-finish')])
        self.assertEqual(engine.writes[-1][1]['room'], ROOM_URL)
        self.assertEqual(live_ops.posts, [])
        # A second look at the same finish reads nothing again.
        await results.record(self.finished('Seed: 9 - Flags: ' + PICK5))
        reader.assert_awaited_once()

    async def test_a_recorded_game_reads_no_log(self):
        # Recorded from this room, or by Council for any room.
        for room, source in ((ROOM_URL, 'room-start'), ('https://racetime.gg/z1r/game-2', 'council')):
            with self.subTest(source=source):
                engine = Engine('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Pick 5'}, score=(1, 0))
                engine.games[2].update(room=room, source=source)
                engine.draw = AsyncMock(return_value={'matches': [{'id': 'W1-25', 'a': 'thomjay',
                                                                   'b': 'specialk3782'}]})
                results, live_ops, reader = self.results(engine, log=self.LOG_ROLLED)
                await results.record(self.finished('Seed: 9 - Flags: ' + PICK5))
                reader.assert_not_awaited()
                self.assertEqual(live_ops.posts, [])

    async def test_the_finish_fallback_records_over_a_retired_room_s_recording(self):
        # A rematch whose start write failed: Game 2's recording is the retired room's.
        engine = Engine('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}, score=(1, 0))
        engine.draw = AsyncMock(return_value={'matches': [{'id': 'W1-25', 'a': 'thomjay', 'b': 'specialk3782'}]})
        results, live_ops, reader = self.results(engine, log=self.LOG_ROLLED)
        await results.record(self.finished('Seed: 9 - Flags: ' + PICK5))
        reader.assert_awaited_once_with(ROOM_URL)
        self.assertEqual(engine.recorded, [('W1-25', 2, PICK5, 'room-finish')])
        self.assertEqual(engine.games[2]['room'], ROOM_URL)

    async def test_a_finished_room_with_a_cancel_in_its_log_records_nothing(self):
        engine = Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0))
        engine.draw = AsyncMock(return_value={'matches': [{'id': 'W1-25', 'a': 'thomjay', 'b': 'specialk3782'}]})
        log = self.LOG_ROLLED + '\n[2027-01-09 00:52:00+00:00] SahasrahBot: ' + RESET
        results, live_ops, _ = self.results(engine, log=log)
        await results.record(self.finished('Seed: 9 - Flags: ' + PICK5))
        self.assertEqual(engine.recorded, [])
        self.assertTrue(live_ops.told('no roll standing'))

    async def test_a_finished_game_still_unrecorded_is_noted_for_the_series_card(self):
        engine = Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0))
        engine.draw = AsyncMock(return_value={'matches': [{'id': 'W1-25', 'a': 'thomjay', 'b': 'specialk3782'}]})
        log = self.LOG_ROLLED + '\n[2027-01-09 00:52:00+00:00] SahasrahBot: ' + RESET
        results, live_ops, _ = self.results(engine, log=log)
        await results.record(self.finished('Seed: 9 - Flags: ' + PICK5))
        self.assertEqual(engine.notes, [{
            'matchId': 'W1-25', 'game': 2, 'room': ROOM_URL, 'code': 'unrecorded',
            'text': 'The room finished with no roll standing, so no flagset was recorded for this game. '
                    'Council needs to record it.',
            'actor': 'ttpbot', 'edition': '5'}])
        self.assertTrue(live_ops.told('no roll standing'))

    async def test_an_unreadable_log_records_nothing_and_warns(self):
        engine = Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0))
        engine.draw = AsyncMock(return_value={'matches': [{'id': 'W1-25', 'a': 'thomjay', 'b': 'specialk3782'}]})
        results, live_ops, _ = self.results(engine, error=RuntimeError('room log returned HTTP 503'))
        await results.record(self.finished('Seed: 9 - Flags: ' + PICK5))
        self.assertEqual(engine.recorded, [])
        self.assertTrue(live_ops.told("the room log couldn't be read"))
        self.assertTrue(live_ops.told('TTP5 W1-25 Game 2 (' + ROOM_URL + ')'))


class Council(unittest.IsolatedAsyncioTestCase):
    async def test_the_room_says_council_was_told_only_after_live_ops_accepts(self):
        for accepts, ending in ((True, ', and Council has been told.'),
                                (False, ". I couldn't reach Council, so please tell them in Discord.")):
            with self.subTest(accepts=accepts):
                room = Room(3, game3_engine(), LiveOps(accepts))
                room.engine.noting = False                   # Live Ops alone
                await room.open()
                await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
                self.assertEqual(len(room.live_ops.posts), 1)
                self.assertTrue(room.said[-1].endswith(ending), room.said[-1])
                self.assertEqual(sum('Council has been told' in line for line in room.said), int(accepts))

    async def test_a_wrong_roll_is_noted_for_the_series_card(self):
        room = Room(3, game3_engine())
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        self.assertEqual(room.engine.notes, [{
            'matchId': 'W1-25', 'game': 3, 'room': 'https://racetime.gg/z1r/ttp-room', 'code': 'wrong-roll',
            'text': 'That seed is Uphill Battle, which was already played. This game should be Pick 5. '
                    '!cancel clears it.',
            'actor': 'ttpbot', 'edition': '5'}])
        # Picked against raced is a wrong roll too.
        picked = Room(1, Engine('defer', chosen={1: 'TTP5 Uphill Battle'}))
        await picked.open()
        await picked.roll('SahasrahBot', info='Seed: 1 - Flags: ' + PICK5)
        self.assertEqual([(n['code'], n['text']) for n in picked.engine.notes], [
            ('wrong-roll', 'That seed is Pick 5, but this game should be Uphill Battle. !cancel clears it.')])

    async def test_each_room_problem_is_noted_with_its_code(self):
        frozen = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        await frozen.open()
        await frozen.roll('SahasrahBot', info='Seed: 1 - Flags: leagueweek8')
        await frozen.start()
        self.assertEqual([n['code'] for n in frozen.engine.notes], ['not-frozen', 'unrecorded'])

        edited = Room(1, Engine('defer', chosen={1: 'TTP5 Uphill Battle'}))
        await edited.open()
        with patch('ttpbot.handler.registry.name_of', return_value='TTP5 Uphill Battle'):
            await edited.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL[:-1] + 'X')
        self.assertEqual([n['code'] for n in edited.engine.notes], ['registry-edit'])

        unrolled = Room(3, game3_engine())
        await unrolled.open()
        await unrolled.start()
        self.assertEqual([(n['code'], n['text']) for n in unrolled.engine.notes],
                         [('no-roll', tr.NO_ROLL_AT_START + '.')])

    async def test_council_counts_as_told_when_only_the_series_card_note_succeeded(self):
        wrong = 'That seed is Uphill Battle, which was already played. This game should be Pick 5. !cancel clears it'
        room = Room(3, game3_engine(), LiveOps(accepts=False))
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        self.assertEqual(len(room.live_ops.posts), 1)
        self.assertEqual(room.said[-1], room.council_said(wrong))
        # Neither: the room asks for Discord.
        room = Room(3, game3_engine(), LiveOps(accepts=False))
        room.engine.noting = False
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        self.assertEqual(room.said[-1], tr.with_council(wrong, False))

    async def test_a_wrong_roll_tells_the_room_and_live_ops_once(self):
        room = Room(3, game3_engine())
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        await room.race_data()
        await room.bot_says('SahasrahBot', ROLLED)
        await room.race_data()
        self.assertEqual(len(room.live_ops.posts), 1)
        self.assertEqual(sum(line.startswith('That seed is Uphill Battle') for line in room.said), 1)
        self.assertEqual(room.live_ops.posts[0],
                         'TTP5 W1-25 Game 3 (https://racetime.gg/z1r/ttp-room): That seed is Uphill Battle, which '
                         'was already played. This game should be Pick 5. !cancel clears it.')

    async def test_a_roll_checked_during_an_engine_blip_is_checked_again(self):
        room = Room(3, game3_engine())
        await room.open()
        room.engine.down = True
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        self.assertEqual(room.said[-1], room.council_said(
            "I can't reach the bracket right now, so picks here can't be saved"))
        room.engine.down = False
        await room.race_data()
        self.assertEqual(room.said[-1], room.council_said(
            'That seed is Uphill Battle, which was already played. This game should be Pick 5. !cancel clears it'))

    async def test_free_text_flags_never_count(self):  # Flags: leagueweek8
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 482059109840868962 - Flags: leagueweek8')
        self.assertIn(room.council_said("That seed isn't one of this tournament's flagsets, so it won't count. "
                                        '!cancel clears it'), room.said)
        await room.start()
        self.assertEqual(room.engine.recorded, [])
        self.assertEqual(room.engine.writes[-1][0], 'recordFlagset')
        self.assertTrue(room.live_ops.told("roll the bracket didn't accept"))

    async def test_a_registry_string_not_on_the_frozen_list_warns(self):  # Review Focus 5
        edited = UPHILL[:-1] + 'X'
        room = Room(1, Engine('defer', chosen={1: 'TTP5 Uphill Battle'}))
        await room.open()
        with patch('ttpbot.handler.registry.name_of', return_value='TTP5 Uphill Battle'):
            await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + edited)
        self.assertIn(room.council_said(
            "That seed is a different Uphill Battle string from the one frozen for this tournament, so it won't "
            'count until Council confirms the change. This game should be Uphill Battle. !cancel clears it'),
            room.said)
        await room.start()
        self.assertEqual(room.engine.recorded, [])

    async def test_engine_down_leaves_the_race_running_and_tells_live_ops(self):
        engine = Engine()
        engine.down = True
        room = Room(1, engine)
        await room.open()
        self.assertTrue(room.live_ops.told("can't reach the bracket"))
        self.assertEqual(room.said[-1], room.council_said(
            "I can't reach the bracket right now, so picks here can't be saved"))
        await room.command('!defer', who='thomjay')
        self.assertEqual(room.said[-1], tr.NOT_SAVED)
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        await room.start()
        self.assertEqual(room.handler.data['status']['value'], 'in_progress')
        self.assertEqual(engine.recorded, [])
        self.assertEqual(sum(line.startswith("I can't reach the bracket") for line in room.said), 1)
        self.assertTrue(room.live_ops.told("couldn't confirm this game's flagset"))


class Picks(unittest.IsolatedAsyncioTestCase):
    async def test_the_spec_s_game_1_room(self):
        room = Room(1)
        await room.open(seeded=False)
        self.assertEqual(room.said, ["Hi, I'm TTPBot. I can help with seed rolling, hash confirmation, and Z1RR "
                                     'links. Type !help to see available commands.'])
        await room.land_seed()
        await room.bot_says('SahasrahBot', "Hi!  I'm SahasrahBot, your friendly robotic elder and randomizer "
                                           'seed roller!')
        await room.command('!defer', who='thomjay')
        await room.command('!ttp5uphill', who='specialk3782')
        await room.command('!flags ' + UPHILL, who='host')                    # SahasrahBot rolls it
        self.assertEqual(room.said[1:], [
            'TTP5 Round 1, Game 1: thomjay vs specialk3782.',
            "thomjay, you're the better seed, so this decision is yours: !pick to choose this game's flagset, "
            'or !defer to give it to specialk3782 and take Game 2.',
            "specialk3782 picks Game 1 and thomjay picks Game 2. specialk3782, when you're ready: "
            '!ttp5uphill !ttp5muffle !ttp5pick5',
            "Game 1 is Uphill Battle. Roll it when everyone's ready: !flags " + UPHILL,
        ])
        self.assertEqual(room.engine.decision, {'choice': 'defer', 'by': 'racer', 'actor': 'thomjay'})
        self.assertEqual(room.engine.chosen[1]['flagset'], 'TTP5 Uphill Battle')
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        self.assertEqual(room.live_ops.posts, [])
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 1, UPHILL, 'room-start')])

    async def test_a_roll_with_no_decision_asks_the_better_seed_and_game_2_names_nobody(self):  # Codex requirement 2
        game1 = Room(1)
        await game1.open()
        await game1.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        self.assertEqual(game1.said[-1], 'thomjay, this seed was rolled before a !pick or !defer. Please confirm '
                                         'with !pick or !defer, so Game 2 has a picker.')
        await game1.start()
        self.assertEqual(game1.engine.recorded, [('W1-25', 1, UPHILL, 'room-start')])
        self.assertTrue(game1.live_ops.told('Council needs to record who picks Game 2'))

        game1.engine.score = (1, 0)
        game2 = Room(2, game1.engine)
        await game2.open()
        self.assertEqual(game2.said[1:], [
            'TTP5 Round 1, Game 2: thomjay leads 1–0.',
            game2.council_said('Nobody has recorded who picks Game 2, so this room names no picker until Council does'),
        ])
        await game2.command('!ttp5muffle', who='thomjay')
        self.assertEqual(game2.said[-1], 'Nobody has recorded who picks Game 2 yet. Council can record it.')
        await game2.command('!pick', who='thomjay')
        self.assertEqual(game2.said[-1], 'Game 1 has started, so Council records that decision now.')
        self.assertIsNone(game1.engine.decision)

    async def test_only_the_better_seed_can_pick_or_defer(self):
        room = Room(1)
        await room.open()
        await room.command('!defer', who='specialk3782')
        await room.command('!pick', who='host')
        self.assertEqual(room.said[-2:], ['Only thomjay, the better seed, decides who picks first.'] * 2)
        self.assertEqual([op for op, _ in room.engine.writes], [])
        await room.command('!pick', who='thomjay')
        self.assertEqual(room.said[-1], "thomjay picks Game 1 and specialk3782 picks Game 2. thomjay, when you're "
                                        'ready: !ttp5uphill !ttp5muffle !ttp5pick5')

    async def test_before_the_match_is_known_picks_wait_and_random_stays_off(self):
        room = Room(1)
        await room.open(seeded=False)
        await room.command('!ttp5uphill', who='thomjay')
        await room.command('!pick', who='thomjay')
        await room.command('!ttp5', who='thomjay')
        waiting = "I don't know this room's match yet, so picks can't be saved. Please try again in a moment."
        self.assertEqual(room.said[-3:], [waiting, waiting, tr.RANDOM_OFF])
        self.assertEqual(room.engine.writes, [])

    async def test_a_name_alone_does_not_make_someone_a_racer(self):
        room = Room(1)
        await room.open()
        message = {'is_bot': False, 'message': '!pick', 'message_plain': '!pick',
                   'user': {'id': 'id-impostor', 'name': 'thomjay'}}
        await room.handler.chat_message({'message': message})
        self.assertEqual(room.said[-1], 'Only thomjay, the better seed, decides who picks first.')

    async def test_flagset_commands_answer_only_the_owner_from_what_is_left(self):
        room = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        await room.open()
        await room.command('!ttp5pick5', who='specialk3782')
        await room.command('!ttp5uphill', who='thomjay')
        await room.command('!ttp5muffle', who='thomjay')
        self.assertEqual(room.said[1:], [
            'TTP5 Round 1, Game 2: thomjay leads 1–0.',
            "thomjay owns this pick. Uphill Battle has been played. When you're ready: !ttp5muffle !ttp5pick5",
            "This one is thomjay's pick.",
            "Uphill Battle has been played. When you're ready: !ttp5muffle !ttp5pick5",
            "Game 2 is Muffle Rug. Roll it when everyone's ready: !flags " + MUFFLE + " If there's a Game 3, it's Pick 5.",
        ])
        self.assertEqual([op for op, _ in room.engine.writes], ['choosePick'])
        self.assertNotIn('Seed rolling complete.  See race info for details.', room.said)

    async def test_game_3_needs_no_pick_and_names_the_flagset_left(self):
        room = Room(3, game3_engine())
        await room.open()
        self.assertEqual(room.said[1:], [
            "TTP5 Round 1, Game 3: tied 1–1. This is Pick 5, the flagset left, so nobody picks. Roll it when "
            "everyone's ready: !flags " + PICK5])
        await room.command('!ttp5uphill', who='thomjay')
        self.assertEqual(room.said[-1], 'Game 3 is Pick 5, the flagset left, so nobody picks.')
        self.assertEqual(room.engine.writes, [])

    REMATCH = ("Game 2 is a rematch on Muffle Rug. Roll it when everyone's ready: !flags " + MUFFLE
               + " If there's a Game 3, it's Pick 5.")

    def rematch_engine(self):
        # Game 2 was raced on Muffle Rug in a room since retired (z1r/game-2).
        return Engine('defer', games={1: 'TTP5 Uphill Battle', 2: 'TTP5 Muffle Rug'}, score=(1, 0))

    async def test_a_rematch_room_names_the_flagset_its_game_was_raced_on(self):
        room = Room(2, self.rematch_engine())
        await room.open()
        self.assertEqual(room.said[1:], ['TTP5 Round 1, Game 2: thomjay leads 1–0.', self.REMATCH])
        await room.command('!defer', who='thomjay')
        self.assertEqual(room.said[-1], 'That decision is already recorded.')
        # A different roll still counts at the start, and is warned about as usual.
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + PICK5)
        self.assertEqual(room.said[-1], room.council_said(
            'That seed is Pick 5, but this game should be Muffle Rug. !cancel clears it'))
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 2, PICK5, 'room-start')])
        self.assertEqual(room.engine.games[2]['room'], 'https://racetime.gg/z1r/ttp-room')

    async def test_a_flagset_command_in_a_rematch_room_answers_with_the_rematch_roll(self):
        room = Room(2, self.rematch_engine())
        await room.open()
        await room.command('!ttp5pick5', who='thomjay')
        await room.command('!ttp5muffle', who='specialk3782')
        self.assertEqual(room.said[-2:], [self.REMATCH, self.REMATCH])
        self.assertEqual(room.engine.writes, [])
        # A room that has itself started keeps its answer.
        started = room.engine.pick_state()
        started['games']['2']['room'] = 'https://racetime.gg/z1r/ttp-room'
        self.assertEqual(
            tr.reply_to_command(started, 2, 'thomjay', 'ttp5pick5', flagsets=FLAGSETS,
                                room='https://racetime.gg/z1r/ttp-room').text,
            'Game 2 has started, so its flagset is the one it was raced on.')

    async def test_random_race_and_presets_do_not_roll_here(self):
        room = Room(1, Engine('defer'))
        await room.open()
        await room.command('!ttp5', who='specialk3782')
        await room.command('!race ttp5uphill', who='host')
        await room.command('!ttp4', who='host')
        await room.command('!consternation', who='host')
        await room.command('!leagueweek1', who='host')
        self.assertEqual(room.said[-5:], [tr.RANDOM_OFF, tr.RACE_OFF, tr.PRESET_OFF, tr.PRESET_OFF, tr.PRESET_OFF])
        self.assertFalse(room.handler.seed_rolled)
        self.assertEqual(room.data['info_bot'], '')

    async def test_flags_stays_free(self):
        room = Room(1, Engine('defer'))
        await room.open()
        await room.command('!flags ' + MUFFLE, who='host')
        self.assertIn('Seed rolling complete.  See race info for details.', room.said)
        self.assertIn('Flags: ' + MUFFLE, room.data['info_bot'])

    async def test_a_restart_mid_room_repeats_nothing(self):  # Review Focus 3
        room = Room(3, game3_engine())
        await room.open()
        await room.roll('SahasrahBot', info='Seed: 1 - Flags: ' + UPHILL)
        posts = list(room.live_ops.posts)
        before = len(room.chat)
        await room.restart()
        await room.race_data()
        await room.land_seed()
        self.assertEqual(room.said, [])
        self.assertEqual(len(room.chat), before)
        self.assertEqual(room.live_ops.posts, posts)

        await room.bot_says('SahasrahBot', RESET)
        await room.roll('SahasrahBot', info='Seed: 2 - Flags: ' + PICK5)
        await room.start()
        self.assertEqual(room.engine.recorded, [('W1-25', 3, PICK5, 'room-start')])
        # A restart mid-race: the history no longer reaches the roll, and the
        # engine already holds this room's recording, so nothing is said.
        room.chat = history(('thomjay', 'gl', True))
        room.said.clear()
        await room.restart()
        self.assertEqual(room.said, [])
        self.assertEqual(room.engine.recorded, [('W1-25', 3, PICK5, 'room-start')])
        self.assertEqual(room.live_ops.posts, posts)

    async def test_a_decision_survives_a_restart(self):
        room = Room(1)
        await room.open()
        await room.command('!defer', who='thomjay')
        await room.restart()
        await room.command('!ttp5muffle', who='specialk3782')
        self.assertEqual(room.said, ["Game 1 is Muffle Rug. Roll it when everyone's ready: !flags " + MUFFLE])

    async def test_reminders_at_15_and_10_stop_after_the_pick_and_never_choose(self):
        room = Room(1)
        await room.open()
        handler = room.handler
        await handler._ttp_tick(START - timedelta(minutes=16))
        await handler._ttp_tick(START - timedelta(minutes=15))
        self.assertEqual(room.said[-1], 'thomjay, the race starts in 15 minutes. Decide with !pick or !defer.')
        await handler._ttp_tick(START - timedelta(minutes=14))
        await room.command('!defer', who='thomjay')
        await room.command('!ttp5pick5', who='specialk3782')
        count = len(room.said)
        await handler._ttp_tick(START - timedelta(minutes=10))
        await handler._ttp_tick(START - timedelta(minutes=5))
        self.assertEqual(len(room.said), count)
        self.assertEqual(sum('the race starts in' in line for line in room.said), 1)
        self.assertEqual([op for op, _ in room.engine.writes], ['decidePick', 'choosePick'])

        owed = Room(2, Engine('defer', games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        await owed.open()
        await owed.handler._ttp_tick(START - timedelta(minutes=15))
        await owed.handler._ttp_tick(START - timedelta(minutes=10))
        self.assertEqual(owed.said[-2:], [
            "thomjay, the race starts in 15 minutes and this game's pick is still yours: !ttp5muffle !ttp5pick5",
            "thomjay, the race starts in 10 minutes and this game's pick is still yours: !ttp5muffle !ttp5pick5"])
        self.assertEqual(owed.engine.writes, [])

    async def test_the_room_names_the_picker_once_council_records_the_decision(self):
        owner = "thomjay owns this pick. Uphill Battle has been played. When you're ready: !ttp5muffle !ttp5pick5"
        no_picker = 'Nobody has recorded who picks Game 2, so this room names no picker until Council does'
        engine = Engine(games={1: 'TTP5 Uphill Battle'}, score=(1, 0))
        room = Room(2, engine)
        await room.open()
        self.assertEqual(room.said[-1], room.council_said(no_picker))
        await room.handler._ttp_tick(START - timedelta(minutes=40))
        self.assertEqual(room.said[-1], room.council_said(no_picker))
        engine.decision = {'choice': 'defer', 'by': 'council', 'actor': 'council'}
        await room.handler._ttp_tick(START - timedelta(minutes=39))
        self.assertEqual(room.said[-1], owner)
        await room.handler._ttp_tick(START - timedelta(minutes=38))
        await room.restart()
        await room.handler._ttp_tick(START - timedelta(minutes=37))
        self.assertEqual(room.said, [])
        self.assertEqual(sum(line.get('message_plain') == owner for line in room.chat), 1)

        # Council records it while TTPBot is down: the restart names the picker, once.
        down = Room(2, Engine(games={1: 'TTP5 Uphill Battle'}, score=(1, 0)))
        await down.open()
        down.engine.decision = {'choice': 'defer', 'by': 'council', 'actor': 'council'}
        await down.restart()
        self.assertEqual(down.said, [owner])

    async def test_a_late_start_skips_reminders_already_past(self):
        room = Room(1)
        await room.open()
        await room.handler._ttp_tick(START - timedelta(minutes=6))
        self.assertFalse(any('the race starts in' in line for line in room.said))


class OtherRooms(unittest.IsolatedAsyncioTestCase):
    async def test_autumn_corto_league_and_nightly_rooms_behave_as_before(self):
        engine = Engine()
        live_ops = LiveOps()
        for flag in ('autumn_room', 'corto_room', 'league_room', 'ttp_scheduled_room'):
            with self.subTest(room=flag):
                handler = TTPRaceHandler(conn=None, logger=LOG, state={'welcomed': True})
                setattr(handler, flag, True)
                handler.ttp_engine, handler.ttp_live_ops = engine, live_ops
                handler.data = {'name': 'z1r/other', 'status': {'value': 'open'}, 'info_bot': '', 'entrants': []}
                said = []

                async def send_message(text, **kwargs):
                    said.append(text)
                handler.send_message = send_message
                handler.set_bot_raceinfo = AsyncMock()
                # A cancelled roll in the history still locks rolling there, as before.
                await handler.chat_history({'messages': history(('SahasrahBot', ROLLED), ('SahasrahBot', RESET))})
                self.assertTrue(handler.seed_rolled)
                self.assertEqual(handler.roll_state, 'none')
                handler.seed_rolled = False
                await handler.chat_message({'message': {'is_bot': True, 'bot': 'SahasrahBot',
                                                        'message_plain': ROLLED}})
                self.assertTrue(handler.seed_rolled)
                handler.sahasrahbot_present = True
                await handler.chat_message({'message': {'message': '!ttp5uphill', 'user': {'name': 'x'}}})
                self.assertEqual(said[-1], 'TTP5 Uphill Battle flags: {0} -- roll with !flags {0}'.format(UPHILL))
                count = len(said)
                await handler.chat_message({'message': {'message': '!pick', 'user': {'name': 'x'}}})
                await handler.chat_message({'message': {'message': '!ttp5', 'user': {'name': 'x'}}})
                self.assertEqual(len(said), count + 1)
                self.assertNotEqual(said[-1], tr.RANDOM_OFF)
                handler.data = dict(handler.data, status={'value': 'in_progress'}, info_bot='Flags: ' + UPHILL)
                await handler.race_data({'race': handler.data})
        self.assertEqual(engine.writes, [])
        self.assertEqual(live_ops.posts, [])


class RealEngine(unittest.IsolatedAsyncioTestCase):
    """The client's pick calls against the engine itself."""

    async def test_picks_through_the_real_engine(self):
        engine_root = os.environ.get('Z1RR_ENGINE_DIR')
        if not engine_root:
            self.skipTest('set Z1RR_ENGINE_DIR for the real engine')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(engine_root).resolve()
        code = '''
import {createEngineService} from SERVICE;
import {createTournamentWriter} from WRITER;
import {createTournamentStore} from STORE;
const storeFor=(event,edition)=>createTournamentStore({event,edition,dir:DIR});
const writer=createTournamentWriter({storeFor});
const server=createEngineService({token:'scratch',storeFor,writer});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
'''
        for key, value in {'SERVICE': (root / 'src/service.mjs').as_uri(), 'WRITER': (root / 'src/writer.mjs').as_uri(),
                           'STORE': (root / 'src/store.mjs').as_uri(), 'DIR': str(Path(temp.name) / 'engine')}.items():
            code = code.replace(key, json.dumps(value))
        process = await asyncio.create_subprocess_exec(
            'node', '--input-type=module', '--eval', code, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        async def stop():
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
        self.addAsyncCleanup(stop)
        port = int(await asyncio.wait_for(process.stdout.readline(), 10))
        engine = AutumnEngine(url='http://127.0.0.1:{}'.format(port), token='scratch', event='ttp', edition='5')
        names = ['thomjay', 'b2', 'c3', 'd4', 'e5', 'f6', 'g7', 'specialk3782']
        for op, body in [('draw', {'seeds': names, 'format': 'single'}), ('migrate', {}),
                         ('series', {'bestOf': 3}),
                         ('setTournamentFlagsets', {'flagsets': FLAGSETS, 'actor': 'council'})]:
            answer = await engine._post(op, body)
            self.assertTrue(answer.ok, (op, answer))

        self.assertEqual(await engine.tournament_flagsets(), FLAGSETS)
        state = await engine.picks('W1-1')
        self.assertEqual((state['betterSeed'], state['otherSeed'], state['decision']), ('thomjay', 'specialk3782', None))
        refused = await engine.decide_pick({'matchId': 'W1-1', 'choice': 'pick', 'actor': 'specialk3782', 'by': 'racer'})
        self.assertEqual(refused.outcome, NOT_RECORDED)
        decided = await engine.decide_pick({'matchId': 'W1-1', 'choice': 'defer', 'actor': 'thomjay', 'by': 'racer'})
        self.assertTrue(decided.ok, decided)
        self.assertEqual(decided.answer['picks']['owners'], {'1': 'specialk3782', '2': 'thomjay'})
        not_owner = await engine.choose_pick({'matchId': 'W1-1', 'game': 1, 'flagset': 'TTP5 Uphill Battle',
                                              'actor': 'thomjay', 'by': 'racer'})
        self.assertEqual(not_owner.outcome, NOT_RECORDED)
        self.assertIn("This one is specialk3782's pick.", not_owner.detail)
        chosen = await engine.choose_pick({'matchId': 'W1-1', 'game': 1, 'flagset': 'TTP5 Uphill Battle',
                                           'actor': 'specialk3782', 'by': 'racer'})
        self.assertTrue(chosen.ok, chosen)
        free_text = await engine.record_flagset({'matchId': 'W1-1', 'game': 1, 'flags': 'leagueweek8', 'room': ROOM_URL,
                                                 'source': 'room-start', 'actor': 'TTPBot'})
        self.assertEqual(free_text.outcome, NOT_RECORDED)
        recorded = await engine.record_flagset({'matchId': 'W1-1', 'game': 1, 'flags': UPHILL, 'room': ROOM_URL,
                                                'source': 'room-start', 'actor': 'TTPBot'})
        self.assertTrue(recorded.ok, recorded)
        again = await engine.record_flagset({'matchId': 'W1-1', 'game': 1, 'flags': UPHILL, 'room': ROOM_URL,
                                             'source': 'room-start', 'actor': 'TTPBot'})
        self.assertTrue(again.ok, again)
        self.assertEqual((await engine.picks('W1-1'))['played'], ['TTP5 Uphill Battle'])

        note = {'matchId': 'W1-1', 'game': 2, 'room': ROOM_URL, 'code': 'wrong-roll',
                'text': 'That seed is Uphill Battle, which was already played. !cancel clears it.', 'actor': 'ttpbot'}
        noted = await engine.note_warning(dict(note))
        self.assertTrue(noted.ok, noted)
        self.assertTrue((await engine.note_warning(dict(note))).ok)
        self.assertEqual([(w['game'], w['room'], w['code'], w['text']) for w in
                          (await engine.picks('W1-1'))['roomWarnings']],
                         [(2, ROOM_URL, 'wrong-roll', note['text'])])

        # A lost answer is settled by reading the series back, not by sending again.
        lost = AutumnEngine(url='http://127.0.0.1:{}'.format(port), token='scratch', event='ttp', edition='5')

        async def lose(operation, payload):
            await AutumnEngine._post(lost, operation, payload)
            return Written(UNCONFIRMED, detail='timeout')
        lost._post = lose
        settled = await lost.choose_pick({'matchId': 'W1-1', 'game': 2, 'flagset': 'TTP5 Pick 5',
                                          'actor': 'thomjay', 'by': 'racer'})
        self.assertEqual(settled.outcome, RECORDED)
        self.assertEqual(settled.detail, 'confirmed by read-back')


if __name__ == '__main__':
    unittest.main()
