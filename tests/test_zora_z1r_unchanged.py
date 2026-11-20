"""The z1r category behaves exactly as it did before zora rooms existed.

`z1r_transcript()` drives z1r rooms through every seed command, preset
shortcut, the help text, chat-history replay and roll/cancel sequences, with a
ZORA preset feed loaded whose shortcuts and strings overlap Council's. Its
output was recorded from `main` before this branch
(tests/fixtures/z1r-transcript-golden.json, made with
`python -m tests.test_zora_z1r_unchanged --record`), and must not change.

Seeds, random picks and the clock are fixed, so the transcript is exact.
"""

import asyncio
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ttpbot.config import TIMEZONE
from ttpbot.flagset_registry import FlagsetRegistry
from ttpbot.handler import TTPRaceHandler
from tests.test_flagset_registry import fresh, snapshot
from tests.test_handler_commands import command_handler

GOLDEN = Path(__file__).parent / 'fixtures' / 'z1r-transcript-golden.json'
NOW = datetime(2026, 11, 17, 21, 0, tzinfo=TIMEZONE)
VALID = 'oIbnPfPb01Hll3D29Bc2!etrojQOSjJQZUJ3A'
TC34 = 'oIbnRjuQKp1DXiYCys9LwvSpzqZbXk1pRmZzr'

# ZORA presets that collide with Council's names, shortcuts and strings. None
# of them may answer, or change an answer, in a z1r room.
ZORA_FEED = {'schemaVersion': 1, 'revision': 1, 'flagsets': [
    {'name': 'Consternation', 'description': None, 'shortcuts': ['consternation', 'tc34', 'zte5'],
     'strings': [{'flags': TC34, 'zora': '13.7Zg4ZxiL', 'isLatest': True}]},
    {'name': 'All Swords No Boards', 'description': None, 'shortcuts': ['asnb', 'ttp5uphill'],
     'strings': [{'flags': '8hq4BeR1JXo7yjOOHjbxryVQW!UJ3A', 'zora': '4.T9Jvg', 'isLatest': True}]},
]}

COMMANDS = [
    f'!flags {VALID}', f'!FLAGS {VALID} 13.7Zg4ZxiL', '!flags', '!flags consternation', '!flags oIbnPf',
    "!flags doesn't", '!flags asnb', '!race consternation', '!race tc34', '!race', '!race zte5', '!race asnb',
    '!race Consternation', '!zte5', '!asnb', '!consternation', '!tc34', '!torneocorto34', '!ttp5uphill',
    '!ttp4rr', '!ttp5', '!ttp4', '!ttp4hopla', '!leagueweek3', '!cancel', '!summary', '!summary zte5',
    '!help', '!z1rr', '!ttpbot', '!matchup a', '!presets', '!lock', '!unlock', '!info', '!ttpflags',
]
SEQUENCES = [
    [f'!flags {VALID}', '!race consternation', '!cancel', '!race tc34', '!cancel', '!cancel'],
    ['!consternation', '!flags ' + VALID, '!summary'],
]
HISTORIES = [
    [],
    ['Seed rolling complete.  See race info for details.'],
    ['Seed rolling complete.  See race info for details.', 'Seed cleared. You may now roll a new one.'],
    ["Hi, I'm TTPBot. I can help with ZORA seed rolling and hash confirmation. Type !help to see available commands."],
]
ROOMS = [
    {'name': 'z1r/casual-room', 'info_bot': '', 'category': {'slug': 'z1r'}},
    {'name': 'z1r/scheduled-room', 'info_bot': 'TTP Season 5 | Scheduled: 9:00 PM EST',
     'category': {'slug': 'z1r'}, 'goal': {'name': 'TTP Season 5'}},
]


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def zora_feed():
    try:
        reader = FlagsetRegistry('https://zora.test/presets.json', None, {}, {}, zora_strings={})
    except TypeError:
        # Recording on main, whose registry has no ZORA mode and no zora rooms to read it.
        reader = FlagsetRegistry('https://zora.test/presets.json', None, {}, {})
    reader.install(ZORA_FEED)
    return reader


def handler_for(room):
    handler = command_handler()
    handler.data = dict(room)
    handler.state = {}
    handler.reminders_sent = set()
    return handler


def record(handler):
    return {'messages': list(handler.messages), 'race_info': list(handler.race_info_updates),
            'seed_rolled': handler.seed_rolled}


async def say(handler, text):
    message = {'message': text, 'message_plain': text, 'user': {'name': 'Racer'}}
    await handler.chat_message({'message': message})
    if handler.race_info_updates:
        handler.data['info_bot'] = handler.race_info_updates[-1]


async def _transcript():
    out = {}
    for room in ROOMS:
        for text in COMMANDS:
            handler = handler_for(room)
            await say(handler, text)
            out[f'{room["name"]} {text}'] = record(handler)
        for number, sequence in enumerate(SEQUENCES):
            handler = handler_for(room)
            for text in sequence:
                await say(handler, text)
            out[f'{room["name"]} sequence {number}'] = record(handler)
        for number, texts in enumerate(HISTORIES):
            handler = handler_for(room)
            handler.ttp_scheduled_room = 'goal' in room
            messages = [{'is_bot': True, 'bot': 'TTPBot', 'message_plain': text} for text in texts]
            await handler.chat_history({'messages': messages})
            await say(handler, '!flags ' + VALID)
            out[f'{room["name"]} history {number}'] = record(handler)
    return out


def z1r_transcript():
    patches = [
        patch('ttpbot.handler.registry', fresh(snapshot())),
        patch('ttpbot.flag_summary.registry', fresh(snapshot())),
        # create=True: main has no zora_registry, and the recording runs there.
        patch('ttpbot.handler.zora_registry', zora_feed(), create=True),
        patch('ttpbot.handler.random.randint', return_value=1234567890123456789),
        patch('ttpbot.handler.random.choice', side_effect=lambda options: list(options)[0]),
        patch('ttpbot.handler.datetime', FixedDatetime),
        patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()),
        patch.object(TTPRaceHandler, '_log_chat'),
        patch.object(TTPRaceHandler, '_handle_recent_history_commands', new=AsyncMock()),
    ]
    for item in patches:
        item.start()
    try:
        return asyncio.run(_transcript())
    finally:
        for item in reversed(patches):
            item.stop()


class Z1rUnchangedTests(unittest.TestCase):
    def test_z1r_rooms_answer_exactly_as_on_main(self):
        golden = json.loads(GOLDEN.read_text(encoding='utf-8'))
        current = z1r_transcript()
        self.assertEqual(sorted(current), sorted(golden))
        for key in golden:
            self.assertEqual(current[key], golden[key], key)

    def test_the_transcript_exercises_rolls_and_refusals(self):
        golden = json.loads(GOLDEN.read_text(encoding='utf-8'))
        self.assertTrue(any(entry['race_info'] for entry in golden.values()))
        self.assertTrue(any(entry['messages'] and not entry['race_info'] for entry in golden.values()))
        self.assertTrue(any(not entry['messages'] for entry in golden.values()), 'some commands are silent')


if __name__ == '__main__':
    if '--record' in sys.argv:
        GOLDEN.write_text(json.dumps(z1r_transcript(), indent=1, sort_keys=True) + '\n', encoding='utf-8')
        print('recorded', GOLDEN)
    else:
        unittest.main()
