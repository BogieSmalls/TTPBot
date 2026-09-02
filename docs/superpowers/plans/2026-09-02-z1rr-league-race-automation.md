# Z1RR League Race Automation — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open a racetime.gg race room for every Z1RR League match on the League schedule spreadsheet, invite both racers, and announce the room in Discord — without changing anything about Triforce Triple Play.

**Architecture:** League runs as a parallel subsystem inside the existing TTPBot process: its own `ttpbot/league/` package, its own scheduler task, its own state files, and its own Discord webhook. It shares the process, the racetime destination (`racetime.gg`/`z1r`), the OAuth credentials, and the handler class. Every edit to an existing file is strictly additive.

**Tech Stack:** Python 3.10+, `racetime_bot==2.3.0`, `aiohttp`, `unittest` (run under `pytest`), `zoneinfo`.

**Spec:** `docs/superpowers/specs/2026-09-02-z1rr-league-race-automation-design.md`

## Global Constraints

These apply to every task. Read them before writing any code.

- **TTP must be unaffected.** Do not modify `TTPBot._create_race_room`, `_recover_uncertain_room`, `_send_webhook`, `_check_and_create_races`, or `race_room_form_data`. Do not change `WEEKLY_SCHEDULE`, `SEASON_START`, `SEASON_END`, `RACE_NUMBER_MAP`, `REMINDER_SCHEDULE`, or any TTP goal/room-info constant. Do not change the schema or contents of `created_races.json` or `sent_webhooks.json`.
- Only three edits land in TTP-shared runtime code, all additive: one `or` clause in `should_handle()`, one `create_task()` in `run()`, and one new branch in `TTPRaceHandler.begin()` that is entered only when League state is present.
- **Never guess a racer.** A row whose runner cannot be resolved is skipped with a logged reason. Wrong racers invited to a live race is worse than no room.
- **Never widen room scope.** `z1r` is shared with the wider Z1R community. The bot handles only rooms it opened.
- Room title (`info_bot`) is exactly `League: X vs. Y`. Goal is exactly `Beat the game`. `invitational` is `'false'`.
- Room opens at T-30. The Discord post fires at T-30, immediately after the room is created.
- `TTPBOT_LEAGUE_ENABLED` defaults to **off**, so deploying this code is a no-op until explicitly switched on.
- Discord mentions are allow-listed: `{"parse": [], "users": [id1, id2]}`. Never `parse: ["users"]`.
- Tests are `unittest.TestCase` classes in `tests/`, run with `uv run pytest`. Follow the existing house style in `tests/test_bot_room_policy.py`.
- `ttpbot/league/roster.json` already exists and is committed. Do not regenerate it.

---

### Task 1: League roster

**Files:**
- Create: `ttpbot/league/__init__.py`
- Create: `ttpbot/league/roster.py`
- Modify: `setup.py` (add `package_data` so `roster.json` ships)
- Test: `tests/test_league_roster.py`
- Existing data: `ttpbot/league/roster.json` (already committed, 42 records)

**Interfaces:**
- Consumes: nothing.
- Produces: `Racer` (frozen dataclass: `sheet_name: str`, `team: str`, `team_full: str`, `display_name: str`, `twitch_channel: str`, `racetime_id: str`, `discord_id: str | None`); `UnknownRacerError(ValueError)`; `RosterError(ValueError)`; `strip_team_prefix(cell: str) -> str`; `Roster.resolve(cell: str) -> Racer`; `Roster.__len__`; `load_roster(path: str | Path | None = None) -> Roster`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_roster.py`:

```python
import unittest

from ttpbot.league.roster import (
    Racer,
    Roster,
    RosterError,
    UnknownRacerError,
    load_roster,
    strip_team_prefix,
)


def _racer(sheet_name, team='SC', racetime_id='abc123', discord_id='1'):
    return Racer(
        sheet_name=sheet_name,
        team=team,
        team_full='Shadow Cartel',
        display_name=sheet_name,
        twitch_channel=sheet_name.lower(),
        racetime_id=racetime_id,
        discord_id=discord_id,
    )


class StripTeamPrefixTests(unittest.TestCase):
    def test_strips_team_prefix_and_whitespace(self):
        self.assertEqual(strip_team_prefix('(SC) Droois'), 'Droois')

    def test_strips_multiword_team_prefix(self):
        self.assertEqual(strip_team_prefix('(8BW) SirLinkalot'), 'SirLinkalot')

    def test_leaves_unprefixed_names_alone(self):
        self.assertEqual(strip_team_prefix('Droois'), 'Droois')

    def test_collapses_internal_whitespace(self):
        self.assertEqual(strip_team_prefix('  (F451)   Rhinohero  '), 'Rhinohero')

    def test_does_not_strip_parenthetical_that_is_not_a_prefix(self):
        self.assertEqual(strip_team_prefix('droois (grindhalo)'), 'droois (grindhalo)')


class RosterResolutionTests(unittest.TestCase):
    def setUp(self):
        self.roster = Roster([_racer('Droois'), _racer('BlessedBe', team='TUG')])

    def test_resolves_exact_name(self):
        self.assertEqual(self.roster.resolve('Droois').sheet_name, 'Droois')

    def test_resolution_is_case_insensitive(self):
        self.assertEqual(self.roster.resolve('droois').sheet_name, 'Droois')

    def test_resolves_through_team_prefix(self):
        self.assertEqual(self.roster.resolve('(SC) Droois').sheet_name, 'Droois')

    def test_unknown_name_raises_rather_than_guessing(self):
        # Never fuzzy-match: inviting the wrong racer is worse than no room.
        with self.assertRaises(UnknownRacerError):
            self.roster.resolve('(SC) Drooi')

    def test_blank_cell_raises(self):
        with self.assertRaises(UnknownRacerError):
            self.roster.resolve('   ')

    def test_duplicate_sheet_names_are_rejected(self):
        with self.assertRaises(RosterError):
            Roster([_racer('Droois'), _racer('droois')])


class ShippedRosterTests(unittest.TestCase):
    def setUp(self):
        self.roster = load_roster()

    def test_ships_all_forty_two_racers(self):
        self.assertEqual(len(self.roster), 42)

    def test_droois_resolves_to_grindhalo_never_droorace(self):
        # crop_profiles holds both "droois (grindhalo)" and "droois (droorace)";
        # only grindhalo is on the League roster.
        droois = self.roster.resolve('(SC) Droois')
        self.assertEqual(droois.twitch_channel, 'grindhalo')
        self.assertEqual(droois.racetime_id, 'NqO2YoLLAbo9QEya')

    def test_resolves_the_six_names_that_differ_from_crop_profiles(self):
        for cell in ('(8BW) Deus Ex Machina', '(F451) Birdman', '(TML) Merks',
                     '(TBC) Sigil', '(TUG) BlessedBe', '(SC) Droois'):
            with self.subTest(cell=cell):
                self.assertIsNotNone(self.roster.resolve(cell).racetime_id)

    def test_every_racer_has_a_racetime_id(self):
        self.assertTrue(all(r.racetime_id for r in self.roster))

    def test_every_racer_has_a_discord_id(self):
        self.assertTrue(all(r.discord_id for r in self.roster))

    def test_racetime_ids_are_unique(self):
        ids = [r.racetime_id for r in self.roster]
        self.assertEqual(len(set(ids)), len(ids))

    def test_roster_is_fourteen_teams_of_three(self):
        teams = {}
        for racer in self.roster:
            teams.setdefault(racer.team, []).append(racer)
        self.assertEqual(len(teams), 14)
        self.assertEqual({len(v) for v in teams.values()}, {3})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_roster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ttpbot.league.roster'`

- [ ] **Step 3: Write minimal implementation**

Create `ttpbot/league/__init__.py`:

```python
"""Z1RR League race automation.

Strictly additive to TTP scheduling: separate state, separate webhook,
separate scheduler task.
"""
```

Create `ttpbot/league/roster.py`:

```python
"""Committed League racer identity.

Identity is resolved from a committed file, never from a live API call at
race time: a cold racetime API or a renamed account must not be able to
break room creation minutes before a race.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Optional


class RosterError(ValueError):
    """The roster file is missing, malformed, or internally inconsistent."""


class UnknownRacerError(ValueError):
    """A schedule cell does not name a known League racer."""


ROSTER_PATH = Path(__file__).resolve().parent / 'roster.json'
TEAM_PREFIX = re.compile(r'^\((?P<team>[^)]{1,20})\)\s+(?P<name>.+)$')
REQUIRED_FIELDS = (
    'sheet_name', 'team', 'team_full', 'display_name',
    'twitch_channel', 'racetime_id',
)


@dataclass(frozen=True)
class Racer:
    sheet_name: str
    team: str
    team_full: str
    display_name: str
    twitch_channel: str
    racetime_id: str
    discord_id: Optional[str]


def strip_team_prefix(cell):
    """Return a schedule cell with its leading '(TEAM) ' prefix removed."""
    if not isinstance(cell, str):
        raise UnknownRacerError('racer cell is not text')
    collapsed = ' '.join(cell.split())
    match = TEAM_PREFIX.match(collapsed)
    return match.group('name') if match else collapsed


class Roster:
    def __init__(self, racers):
        racers = list(racers)
        index = {}
        for racer in racers:
            key = racer.sheet_name.strip().lower()
            if key in index:
                raise RosterError(
                    'duplicate roster sheet name: {}'.format(racer.sheet_name)
                )
            index[key] = racer
        self._racers = racers
        self._index = index

    def __len__(self):
        return len(self._racers)

    def __iter__(self):
        return iter(self._racers)

    def resolve(self, cell):
        """Resolve a schedule cell to a Racer, or raise. Never fuzzy-matches."""
        name = strip_team_prefix(cell)
        if not name:
            raise UnknownRacerError('racer cell is empty')
        try:
            return self._index[name.lower()]
        except KeyError:
            raise UnknownRacerError('unknown League racer: {!r}'.format(name)) from None


def load_roster(path=None):
    """Load and validate the committed roster."""
    target = ROSTER_PATH if path is None else Path(path)
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except OSError as exc:
        raise RosterError('roster file could not be read') from exc
    except json.JSONDecodeError as exc:
        raise RosterError('roster file is not valid JSON') from exc
    if not isinstance(raw, list) or not raw:
        raise RosterError('roster file must be a non-empty list')

    racers = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise RosterError('roster entries must be objects')
        for field in REQUIRED_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RosterError('roster entry is missing {}'.format(field))
        discord_id = entry.get('discord_id')
        if discord_id is not None:
            if not isinstance(discord_id, str) or not discord_id.isdigit():
                raise RosterError('roster discord_id must be digits or null')
        racers.append(Racer(
            sheet_name=entry['sheet_name'],
            team=entry['team'],
            team_full=entry['team_full'],
            display_name=entry['display_name'],
            twitch_channel=entry['twitch_channel'],
            racetime_id=entry['racetime_id'],
            discord_id=discord_id or None,
        ))
    return Roster(racers)
```

Modify `setup.py` — add `package_data` and `include_package_data` so the roster ships with an installed package. Change the `setup(` call to include:

```python
    packages=find_packages(),
    package_data={'ttpbot.league': ['roster.json']},
    include_package_data=True,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_roster.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/league/__init__.py ttpbot/league/roster.py setup.py tests/test_league_roster.py
git commit -m "feat: add League roster loading and name resolution"
```

---

### Task 2: League schedule parsing

**Files:**
- Create: `ttpbot/league/schedule.py`
- Modify: `ttpbot/config.py` (add one constant)
- Test: `tests/test_league_schedule.py`

**Interfaces:**
- Consumes: `Racer`, `Roster`, `UnknownRacerError` from Task 1.
- Produces: `LeagueRace` (frozen dataclass: `start: datetime`, `runner_one: Racer`, `runner_two: Racer`, `channel: str | None`) with properties `slug -> str`, `key -> str`, `title -> str`; `parse_schedule(csv_text: str, roster: Roster, logger) -> list[LeagueRace]`; and the constant `LEAGUE_ROOM_INFO_PREFIX = 'League: '` **in `ttpbot/config.py`**.

**Where the room-title prefix lives.** `LEAGUE_ROOM_INFO_PREFIX` goes in `ttpbot/config.py`, beside its siblings `REGULAR_SEASON_ROOM_INFO_PREFIX` and `POST_SEASON_ROOM_INFO_PREFIX` — not in this module. Task 4 needs it in `ttpbot/room_policy.py`, which is core code; importing it from the `league` subpackage would make core depend on a feature package. Adding a constant to `config.py` is additive and does not alter any TTP value.

**Why the key sorts but the title does not:** the key identifies *which two racers* are in a match, so swapping the runner columns in the sheet must not produce a second room. The title reads the way the schedule reads.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_schedule.py`:

```python
import logging
import unittest
from datetime import datetime

from ttpbot.config import TIMEZONE
from ttpbot.league.roster import Racer, Roster
from ttpbot.league.schedule import LeagueRace, parse_schedule

HEADER = 'Date,Time,Game,Runner 1,Runner 2,,Comms,Tracker,,Channel,Booth\n'

QUIET = logging.getLogger('test-league-schedule')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


def _racer(name, team, racetime_id, discord_id='1'):
    return Racer(
        sheet_name=name, team=team, team_full=team,
        display_name=name, twitch_channel=name.lower(),
        racetime_id=racetime_id, discord_id=discord_id,
    )


ROSTER = Roster([
    _racer('Droois', 'SC', 'rt-droois'),
    _racer('Rhinohero', 'F451', 'rt-rhino'),
    _racer('seanfreston', 'DAMB', 'rt-sean'),
    _racer('Stags28', '3HM', 'rt-stags'),
    _racer('SirLinkalot', '8BW', 'rt-sir'),
    _racer('Windfox470', 'BM', 'rt-wind'),
])


class ParseScheduleTests(unittest.TestCase):
    def parse(self, body):
        return parse_schedule(HEADER + body, ROSTER, QUIET)

    def test_parses_a_row_into_a_league_race(self):
        races = self.parse(
            '9/2/2026,9:00:00 PM,2,(SC) Droois,(F451) Rhinohero,,Sean,,,Z1Rracing,Sean\n'
        )

        self.assertEqual(len(races), 1)
        race = races[0]
        self.assertEqual(race.start, datetime(2026, 9, 2, 21, 0, tzinfo=TIMEZONE))
        self.assertEqual(race.runner_one.sheet_name, 'Droois')
        self.assertEqual(race.runner_two.sheet_name, 'Rhinohero')
        self.assertEqual(race.channel, 'Z1Rracing')

    def test_blank_channel_becomes_none(self):
        races = self.parse('9/3/2026,9:00:00 PM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n')
        self.assertIsNone(races[0].channel)

    def test_two_races_in_one_slot_both_survive(self):
        # League nights run 1v1 and 2v2, so concurrent rooms are normal.
        races = self.parse(
            '9/3/2026,8:00:00 PM,1,(DAMB) seanfreston,(3HM) Stags28,,,,,,\n'
            '9/3/2026,8:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(len(races), 2)
        self.assertEqual(races[0].start, races[1].start)
        self.assertNotEqual(races[0].key, races[1].key)

    def test_unresolvable_runner_skips_only_that_row(self):
        races = self.parse(
            '9/3/2026,8:00:00 PM,1,(XX) NotARacer,(3HM) Stags28,,,,,,\n'
            '9/3/2026,9:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(len(races), 1)
        self.assertEqual(races[0].runner_one.sheet_name, 'SirLinkalot')

    def test_malformed_date_skips_only_that_row(self):
        races = self.parse(
            'not-a-date,8:00:00 PM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n'
            '9/3/2026,9:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )
        self.assertEqual(len(races), 1)

    def test_short_and_blank_rows_are_ignored(self):
        races = self.parse(',,,,,,,,,,\n9/3/2026\n')
        self.assertEqual(races, [])

    def test_same_racer_twice_is_rejected(self):
        races = self.parse('9/3/2026,8:00:00 PM,1,(SC) Droois,(SC) Droois,,,,,,\n')
        self.assertEqual(races, [])

    def test_handles_daylight_saving_edges(self):
        # 2026-11-01 is the US Eastern fall-back date; 2026-03-08 is spring-forward.
        races = self.parse(
            '11/1/2026,9:00:00 PM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n'
            '3/8/2026,9:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(len(races), 2)
        self.assertEqual(races[0].start.utcoffset().total_seconds(), -5 * 3600)
        self.assertEqual(races[1].start.utcoffset().total_seconds(), -4 * 3600)

    def test_accepts_midnight_and_noon(self):
        races = self.parse(
            '9/3/2026,12:00:00 AM,1,(SC) Droois,(F451) Rhinohero,,,,,,\n'
            '9/3/2026,12:00:00 PM,1,(8BW) SirLinkalot,(BM) Windfox470,,,,,,\n'
        )

        self.assertEqual(races[0].start.hour, 0)
        self.assertEqual(races[1].start.hour, 12)


class LeagueRaceIdentityTests(unittest.TestCase):
    def make(self, one, two):
        return LeagueRace(
            start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
            runner_one=ROSTER.resolve(one),
            runner_two=ROSTER.resolve(two),
            channel=None,
        )

    def test_title_uses_sheet_column_order(self):
        race = self.make('Windfox470', 'SirLinkalot')
        self.assertEqual(race.title, 'League: Windfox470 vs. SirLinkalot')

    def test_key_is_independent_of_runner_column_order(self):
        # Swapping the two runner columns must not open a second room.
        self.assertEqual(
            self.make('SirLinkalot', 'Windfox470').key,
            self.make('Windfox470', 'SirLinkalot').key,
        )

    def test_key_starts_with_an_iso_timestamp_then_a_pipe(self):
        key = self.make('SirLinkalot', 'Windfox470').key
        stamp, _, slug = key.partition('|')
        self.assertEqual(datetime.fromisoformat(stamp).hour, 20)
        self.assertEqual(slug, 'sirlinkalot-vs-windfox470')

    def test_slug_is_lowercase_alphanumeric_and_dashes(self):
        slug = self.make('SirLinkalot', 'Windfox470').slug
        self.assertRegex(slug, r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_schedule.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ttpbot.league.schedule'`

- [ ] **Step 3: Write minimal implementation**

First, in `ttpbot/config.py`, add the constant immediately after `TTP_ROOM_INFO_PREFIXES`:

```python
# Z1RR League rooms. Separate from the TTP prefixes above: League rooms
# share the "Beat the game" goal, so this prefix is what distinguishes them.
LEAGUE_ROOM_INFO_PREFIX = "League: "
```

Change nothing else in `config.py`.

Then create `ttpbot/league/schedule.py`:

```python
"""Parse the League schedule spreadsheet into races.

The sheet is a live document. Every cell is untrusted input: parsing is
total, and a bad row is skipped with a logged reason rather than allowed
to stop the rows around it.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
import io
import re
from typing import Optional

from ..config import LEAGUE_ROOM_INFO_PREFIX, TIMEZONE
from .roster import Racer, UnknownRacerError

DATE_FORMAT = '%m/%d/%Y'
TIME_FORMATS = ('%I:%M:%S %p', '%I:%M %p', '%H:%M:%S', '%H:%M')
NON_SLUG = re.compile(r'[^a-z0-9]+')

COLUMN_DATE = 0
COLUMN_TIME = 1
COLUMN_RUNNER_ONE = 3
COLUMN_RUNNER_TWO = 4
COLUMN_CHANNEL = 9
MINIMUM_COLUMNS = 5


def _slugify(value):
    return NON_SLUG.sub('-', value.strip().lower()).strip('-')


@dataclass(frozen=True)
class LeagueRace:
    start: datetime
    runner_one: Racer
    runner_two: Racer
    channel: Optional[str] = None

    @property
    def slug(self):
        """Order-independent identity: which two racers are in this match."""
        names = sorted(
            _slugify(r.sheet_name) for r in (self.runner_one, self.runner_two)
        )
        return '{}-vs-{}'.format(names[0], names[1])

    @property
    def key(self):
        """Idempotency key: '<iso start>|<slug>'.

        The timestamp prefix keeps the state store's age-based cleanup
        working; ISO-8601 timestamps never contain '|'.
        """
        return '{}|{}'.format(self.start.isoformat(), self.slug)

    @property
    def title(self):
        """Room info_bot, in sheet column order."""
        return '{}{} vs. {}'.format(
            LEAGUE_ROOM_INFO_PREFIX,
            self.runner_one.display_name,
            self.runner_two.display_name,
        )


def _parse_start(date_text, time_text):
    day = datetime.strptime(date_text.strip(), DATE_FORMAT).date()
    for fmt in TIME_FORMATS:
        try:
            clock = datetime.strptime(time_text.strip().upper(), fmt).time()
        except ValueError:
            continue
        return datetime.combine(day, clock, tzinfo=TIMEZONE)
    raise ValueError('unrecognised time: {!r}'.format(time_text))


def parse_schedule(csv_text, roster, logger):
    """Return every well-formed, fully resolvable race in the sheet."""
    races = []
    rows = list(csv.reader(io.StringIO(csv_text)))
    for number, row in enumerate(rows[1:], start=2):
        if len(row) < MINIMUM_COLUMNS:
            continue
        if not any(cell.strip() for cell in row):
            continue
        try:
            start = _parse_start(row[COLUMN_DATE], row[COLUMN_TIME])
        except ValueError as exc:
            logger.warning('League row %d skipped: %s', number, exc)
            continue
        try:
            one = roster.resolve(row[COLUMN_RUNNER_ONE])
            two = roster.resolve(row[COLUMN_RUNNER_TWO])
        except UnknownRacerError as exc:
            logger.warning('League row %d skipped: %s', number, exc)
            continue
        if one.racetime_id == two.racetime_id:
            logger.warning(
                'League row %d skipped: both runners resolve to %s',
                number, one.sheet_name,
            )
            continue
        channel = row[COLUMN_CHANNEL].strip() if len(row) > COLUMN_CHANNEL else ''
        races.append(LeagueRace(
            start=start, runner_one=one, runner_two=two,
            channel=channel or None,
        ))
    return races
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_schedule.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/config.py ttpbot/league/schedule.py tests/test_league_schedule.py
git commit -m "feat: parse the League schedule sheet into races"
```

---

### Task 3: League state kinds

**Files:**
- Modify: `ttpbot/state.py` (`ENTRY_KINDS`, `_validate_key`, `_validate_entries`, `cleanup_before`)
- Test: `tests/test_league_state.py`

**Interfaces:**
- Consumes: `LeagueRace.key` format from Task 2.
- Produces: entry kinds `league_created_races` and `league_sent_webhooks` usable with the existing `DestinationStateStore`.

TTP's two kinds keep their current validation exactly — including the 100-character key limit and the rejection of any key containing `|`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_state.py`:

```python
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ttpbot.state import DestinationStateStore, StateStoreError

DESTINATION = 'https://racetime.gg|z1r'
KEY = '2026-09-03T20:00:00-04:00|sirlinkalot-vs-windfox470'
OTHER_KEY = '2026-09-03T20:00:00-04:00|seanfreston-vs-stags28'
ROOM = 'https://racetime.gg/z1r/clever-slug-1234'


class LeagueStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def store(self, kind='league_created_races', name='league_races.json'):
        return DestinationStateStore(name, DESTINATION, kind, data_dir=self.root)

    def test_round_trips_a_league_key(self):
        store = self.store()
        store.save({KEY: ROOM})
        self.assertEqual(store.load(), {KEY: ROOM})

    def test_two_same_slot_races_coexist(self):
        store = self.store()
        store.save({KEY: ROOM, OTHER_KEY: ROOM})
        self.assertEqual(len(store.load()), 2)

    def test_webhook_kind_accepts_league_keys(self):
        store = self.store('league_sent_webhooks', 'league_webhooks.json')
        store.save({KEY: True})
        self.assertEqual(store.load(), {KEY: True})

    def test_rejects_a_league_key_without_a_slug(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00-04:00': ROOM})

    def test_rejects_a_league_slug_with_illegal_characters(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00-04:00|bad slug': ROOM})

    def test_rejects_a_naive_timestamp(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00|a-vs-b': ROOM})

    def test_still_validates_room_urls_against_the_destination(self):
        with self.assertRaises(StateStoreError):
            self.store().save({KEY: 'https://example.com/z1r/room'})

    def test_cleanup_ages_out_league_keys_by_timestamp(self):
        store = self.store()
        old = '2020-01-01T20:00:00-05:00|a-vs-b'
        store.save({KEY: ROOM, old: ROOM})

        retained = store.cleanup_before(datetime.now(timezone.utc) - timedelta(hours=2))

        self.assertIn(KEY, retained)
        self.assertNotIn(old, retained)

    def test_ttp_store_rejects_a_league_key(self):
        ttp = self.store('created_races', 'created_races.json')
        with self.assertRaises(StateStoreError):
            ttp.save({KEY: ROOM})

    def test_league_store_rejects_a_bare_timestamp_ttp_key(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00-04:00': ROOM})

    def test_ttp_store_still_round_trips_its_own_keys(self):
        ttp = self.store('created_races', 'created_races.json')
        ttp.save({'2026-09-03T20:00:00-04:00': ROOM})
        self.assertEqual(ttp.load(), {'2026-09-03T20:00:00-04:00': ROOM})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_state.py -v`
Expected: FAIL — `StateStoreError: state entry kind is invalid`

- [ ] **Step 3: Write minimal implementation**

In `ttpbot/state.py`, add `import re` to the imports, then replace the `ENTRY_KINDS` line:

```python
LEAGUE_ENTRY_KINDS = {"league_created_races", "league_sent_webhooks"}
CREATED_ENTRY_KINDS = {"created_races", "league_created_races"}
ENTRY_KINDS = {"created_races", "sent_webhooks"} | LEAGUE_ENTRY_KINDS
LEAGUE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_LEAGUE_KEY_LENGTH = 200
MAX_KEY_LENGTH = 100
```

Replace `_validate_key` with:

```python
    def _key_timestamp(self, value):
        """Return the ISO timestamp portion of a state key."""
        if self.entry_kind in LEAGUE_ENTRY_KINDS:
            return value.partition("|")[0]
        return value

    def _validate_key(self, value):
        league = self.entry_kind in LEAGUE_ENTRY_KINDS
        limit = MAX_LEAGUE_KEY_LENGTH if league else MAX_KEY_LENGTH
        if not isinstance(value, str) or len(value) > limit:
            raise StateStoreError("state entry key is invalid")
        if league:
            timestamp, separator, slug = value.partition("|")
            if not separator or not LEAGUE_SLUG.fullmatch(slug):
                raise StateStoreError("league state entry key is invalid")
        else:
            if "|" in value:
                raise StateStoreError("state entry key is invalid")
            timestamp = value
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise StateStoreError("state entry key must be an ISO timestamp") from exc
        if parsed.tzinfo is None:
            raise StateStoreError("state entry key must include a timezone")
```

In `_validate_entries`, replace the created-races test:

```python
            if self.entry_kind in CREATED_ENTRY_KINDS:
```

In `cleanup_before`, replace the retention comprehension:

```python
        retained = {
            key: value
            for key, value in entries.items()
            if datetime.fromisoformat(self._key_timestamp(key)) > cutoff
        }
```

In `migrate_legacy`, replace the created-races test the same way:

```python
        if self.entry_kind in CREATED_ENTRY_KINDS:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_state.py tests/test_state.py tests/test_migrate_state.py -v`
Expected: PASS. The existing state tests must all still pass unchanged — that is the TTP regression guard for this task.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/state.py tests/test_league_state.py
git commit -m "feat: allow per-race League keys in the destination state store"
```

---

### Task 4: League room recognition

**Files:**
- Modify: `ttpbot/room_policy.py`
- Modify: `ttpbot/bot.py` (`should_handle` only)
- Test: `tests/test_league_room_policy.py`

**Interfaces:**
- Consumes: `LEAGUE_ROOM_INFO_PREFIX` from Task 2.
- Produces: `is_league_room(race_data: dict) -> bool`, importable from both `ttpbot.room_policy` and `ttpbot.bot`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_room_policy.py`:

```python
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


class LeagueRoomPolicyTests(unittest.TestCase):
    def test_recognises_a_league_room(self):
        self.assertTrue(is_league_room(LEAGUE))

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_room_policy.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_league_room'`

- [ ] **Step 3: Write minimal implementation**

In `ttpbot/room_policy.py`, extend the existing `.config` import to include `LEAGUE_ROOM_INFO_PREFIX` (Task 2 added it there), so the import block becomes:

```python
from .config import (
    GOAL_NAME,
    LEAGUE_ROOM_INFO_PREFIX,
    POST_SEASON_GOAL_NAME,
    TTP_ROOM_INFO_PREFIXES,
)
```

Do not import from the `league` subpackage here — `room_policy` is core code and must not depend on a feature package. Then add the function:

```python
def is_league_room(race_data):
    """Return True for Z1RR League rooms this bot scheduled.

    League rooms share the 'Beat the game' goal with TTP post-season rooms,
    so the info_bot prefix is what separates them. The prefix is one this
    automation writes itself, so a community room cannot match.
    """
    goal_name = race_data.get('goal', {}).get('name', '')
    if goal_name != POST_SEASON_GOAL_NAME:
        return False
    info_bot = race_data.get('info_bot', '') or ''
    return info_bot.startswith(LEAGUE_ROOM_INFO_PREFIX)
```

In `ttpbot/bot.py`, change the import line `from .room_policy import is_ttp_scheduled_room` to:

```python
from .room_policy import is_league_room, is_ttp_scheduled_room
```

and change `should_handle` to:

```python
    def should_handle(self, race_data):
        """Handle only rooms this bot scheduled.

        The z1r category on racetime.gg is shared with the wider Z1R
        community, so TTPBot stays out of races it did not open.
        """
        if not super().should_handle(race_data):
            return False
        return is_ttp_scheduled_room(race_data) or is_league_room(race_data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_room_policy.py tests/test_bot_room_policy.py -v`
Expected: PASS. `test_bot_room_policy.py` must pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/room_policy.py ttpbot/bot.py tests/test_league_room_policy.py
git commit -m "feat: recognise League rooms so the bot joins them"
```

---

### Task 5: League invites in the handler

**Files:**
- Modify: `ttpbot/handler.py` (`__init__`, `begin`)
- Test: `tests/test_league_handler.py`

**Interfaces:**
- Consumes: `is_league_room` (Task 4), `load_roster` (Task 1), `LEAGUE_ROOM_INFO_PREFIX` (Task 2).
- Produces: `TTPRaceHandler.league_room: bool`, `TTPRaceHandler.league_invited: bool`, `TTPRaceHandler._league_invite_ids() -> list[str]`.

**Reminders need no new suppression.** `begin()` already schedules reminders only inside `if self.ttp_scheduled_room:`, and `is_ttp_scheduled_room` is False for a League room, so a League room takes the `else` branch and gets none. Assert it; do not add logic for it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_handler.py`:

```python
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
    handler.league_invited = False
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_handler.py -v`
Expected: FAIL — `AttributeError` on `league_room`, and no invites sent.

- [ ] **Step 3: Write minimal implementation**

In `ttpbot/handler.py`, extend the room-policy import:

```python
from .room_policy import is_league_room, is_ttp_scheduled_room
```

add `LEAGUE_ROOM_INFO_PREFIX` to the existing `from .config import (...)` block (keeping it alphabetical among the names already imported there), and add below the existing imports:

```python
from .league.roster import RosterError, UnknownRacerError, load_roster
```

In `__init__`, after `self.ttp_scheduled_room = False`, add:

```python
        self.league_room = False
        self.league_invited = False
```

Add these two methods to `TTPRaceHandler`:

```python
    def _league_invite_ids(self):
        """Return the racetime ids to invite in this League room.

        Prefers state seeded by the scheduler at room creation. After a
        restart that state is gone, so fall back to the room title, which
        this automation wrote itself.
        """
        seeded = (self.state or {}).get('league_race') or {}
        invite = seeded.get('invite')
        if isinstance(invite, list) and all(isinstance(i, str) and i for i in invite):
            return list(invite)

        info_bot = self.data.get('info_bot', '') or ''
        pairing = info_bot[len(LEAGUE_ROOM_INFO_PREFIX):]
        names = pairing.split(' vs. ')
        if len(names) != 2:
            self.logger.warning('[%s] League title is unparseable: %r',
                                self.data.get('name'), info_bot)
            return []
        try:
            roster = load_roster()
            return [roster.resolve(name).racetime_id for name in names]
        except (RosterError, UnknownRacerError) as exc:
            self.logger.warning('[%s] League invites unresolved: %s',
                                self.data.get('name'), exc)
            return []

    async def _send_league_invites(self):
        """Invite both racers exactly once."""
        if self.league_invited:
            return
        invite_ids = self._league_invite_ids()
        if not invite_ids:
            return
        # Set before awaiting so a concurrent begin() cannot double-invite.
        self.league_invited = True
        for racetime_id in invite_ids:
            await self.invite_user(racetime_id)
        self.logger.info('[%s] invited %d League racers',
                         self.data.get('name'), len(invite_ids))
```

In `begin()`, change the first line and the `else` branch. The method becomes:

```python
    async def begin(self):
        self.ttp_scheduled_room = is_ttp_scheduled_room(self.data)
        self.league_room = is_league_room(self.data)
        self.history_command_cutoff_utc = self._recent_room_history_cutoff()

        if self.ttp_scheduled_room:
            ...unchanged TTP block...
        else:
            self.scheduled_time = None
            self.bot_created = False
            if self.league_room:
                await self._send_league_invites()

        # Request chat history to detect prior seed rolls and, for TTP rooms,
        # avoid duplicate welcomes/reminders.
        await self.ws.send(json.dumps({'action': 'gethistory'}))
```

Leave the TTP block byte-for-byte as it is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_handler.py tests/test_handler_commands.py -v`
Expected: PASS. `test_handler_commands.py` must pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/handler.py tests/test_league_handler.py
git commit -m "feat: invite League racers when the bot joins the room"
```

---

### Task 6: League room creation

**Files:**
- Create: `ttpbot/league/rooms.py`
- Test: `tests/test_league_rooms.py`

**Interfaces:**
- Consumes: `LeagueRace` (Task 2), `RacetimeProvider`/`ProviderConfigurationError` (existing `ttpbot/provider.py`), `UNCERTAIN_RACE` (existing `ttpbot/state.py`).
- Produces: `league_room_form_data(race: LeagueRace) -> dict`; `async create_league_room(race, provider, access_token, logger) -> str | None` returning a room URL, `UNCERTAIN_RACE`, or `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_rooms.py`:

```python
import unittest
from datetime import datetime

from ttpbot.config import POST_SEASON_GOAL_NAME, TIMEZONE
from ttpbot.league.rooms import league_room_form_data
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace


def _racer(name, racetime_id):
    return Racer(
        sheet_name=name, team='SC', team_full='Shadow Cartel',
        display_name=name, twitch_channel=name.lower(),
        racetime_id=racetime_id, discord_id='1',
    )


RACE = LeagueRace(
    start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
    runner_one=_racer('SirLinkalot', 'rt-sir'),
    runner_two=_racer('Windfox470', 'rt-wind'),
    channel=None,
)


class LeagueRoomFormTests(unittest.TestCase):
    def setUp(self):
        self.form = league_room_form_data(RACE)

    def test_uses_the_beat_the_game_goal(self):
        self.assertEqual(self.form['goal'], POST_SEASON_GOAL_NAME)

    def test_titles_the_room_with_both_racers(self):
        self.assertEqual(self.form['info_bot'], 'League: SirLinkalot vs. Windfox470')

    def test_room_is_open_so_commentators_can_join(self):
        self.assertEqual(self.form['invitational'], 'false')

    def test_room_is_listed(self):
        self.assertEqual(self.form['unlisted'], 'false')

    def test_every_value_is_a_string(self):
        self.assertTrue(all(isinstance(v, str) for v in self.form.values()))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_rooms.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ttpbot.league.rooms'`

- [ ] **Step 3: Write minimal implementation**

Create `ttpbot/league/rooms.py`:

```python
"""Create League race rooms.

Deliberately separate from TTPBot._create_race_room. The duplication is
worth it: TTP Season 5 is live, and League must not be able to change how
TTP rooms are opened.
"""

import asyncio

import aiohttp

from ..config import POST_SEASON_GOAL_NAME
from ..provider import ProviderConfigurationError
from ..state import UNCERTAIN_RACE


def league_room_form_data(race):
    """Return Racetime form fields for a League room."""
    return {
        'goal': POST_SEASON_GOAL_NAME,
        'info_bot': race.title,
        'invitational': 'false',
        'unlisted': 'false',
        'start_delay': '15',
        'time_limit': '4',
        'streaming_required': 'true',
        'auto_start': 'true',
        'allow_prerace_chat': 'true',
        'allow_midrace_chat': 'true',
        'allow_non_entrant_chat': 'true',
        'chat_message_delay': '0',
        'hide_comments': 'true',
    }


async def create_league_room(race, provider, access_token, logger):
    """Open a League room. Returns a URL, UNCERTAIN_RACE, or None."""
    logger.info('Creating League room: %s', race.title)
    try:
        async with aiohttp.request(
            method='post',
            url=provider.http_url('/o/{}/startrace'.format(provider.category)),
            headers={
                'Authorization': 'Bearer {}'.format(access_token),
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data=league_room_form_data(race),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status == 201:
                room_url = provider.resolve_location(response.headers.get('Location'))
                logger.info('League room created: %s', room_url)
                return room_url
            logger.error('Provider rejected League room creation (HTTP %d)',
                         response.status)
    except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as exc:
        recovered = await _recover_uncertain_league_room(race, provider,
                                                        access_token, logger)
        if recovered:
            logger.warning('Recovered League room after uncertain response')
            return recovered
        logger.error(
            'League room creation is uncertain; persisted fail-closed marker (%s)',
            type(exc).__name__,
        )
        return UNCERTAIN_RACE
    except (ProviderConfigurationError, aiohttp.ClientError, TypeError) as exc:
        logger.error('League room creation failed safely (%s)', type(exc).__name__)
    return None


async def _recover_uncertain_league_room(race, provider, access_token, logger):
    """Read current rooms once; never blindly retry an uncertain POST."""
    try:
        async with aiohttp.request(
            method='get',
            url=provider.http_url('/{}/data'.format(provider.category)),
            headers={'Authorization': 'Bearer {}'.format(access_token)},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
        races = data.get('current_races', []) if isinstance(data, dict) else []
        matches = []
        for candidate in races:
            if not isinstance(candidate, dict):
                continue
            if candidate.get('info_bot') != race.title:
                continue
            raw_url = candidate.get('url')
            if not raw_url and isinstance(candidate.get('name'), str):
                raw_url = '/' + candidate['name'].lstrip('/')
            matches.append(provider.resolve_location(raw_url))
        unique = sorted(set(matches))
        return unique[0] if len(unique) == 1 else None
    except (ProviderConfigurationError, aiohttp.ClientError,
            asyncio.TimeoutError, TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_rooms.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/league/rooms.py tests/test_league_rooms.py
git commit -m "feat: create League race rooms"
```

---

### Task 7: League Discord announcement

**Files:**
- Create: `ttpbot/league/announce.py`
- Test: `tests/test_league_announce.py`

**Interfaces:**
- Consumes: `LeagueRace` (Task 2).
- Produces: `build_announcement(race: LeagueRace, race_url: str) -> dict` (the full webhook JSON body); `async send_league_announcement(race, race_url, webhook_url, logger) -> bool`.

**Security requirement:** the message is assembled from a live spreadsheet, so `parse` is always `[]` and `users` contains only the two racers' ids. A stray `@everyone` in a cell must be inert.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_announce.py`:

```python
import unittest
from datetime import datetime

from ttpbot.config import TIMEZONE
from ttpbot.league.announce import build_announcement
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace

ROOM = 'https://racetime.gg/z1r/clever-slug-1234'


def _racer(name, discord_id):
    return Racer(
        sheet_name=name, team='SC', team_full='Shadow Cartel',
        display_name=name, twitch_channel=name.lower(),
        racetime_id='rt-' + name.lower(), discord_id=discord_id,
    )


def _race(one, two):
    return LeagueRace(
        start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
        runner_one=one, runner_two=two, channel=None,
    )


class BuildAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.race = _race(_racer('SirLinkalot', '111'), _racer('Windfox470', '222'))
        self.body = build_announcement(self.race, ROOM)

    def test_mentions_both_racers(self):
        self.assertIn('<@111>', self.body['content'])
        self.assertIn('<@222>', self.body['content'])

    def test_includes_the_room_url(self):
        self.assertIn(ROOM, self.body['content'])

    def test_allow_lists_exactly_the_two_racers(self):
        self.assertEqual(self.body['allowed_mentions'],
                         {'parse': [], 'users': ['111', '222']})

    def test_parse_is_always_empty(self):
        # A stray @everyone in a spreadsheet cell must not ping the server.
        race = _race(_racer('@everyone', '111'), _racer('Windfox470', '222'))
        body = build_announcement(race, ROOM)
        self.assertEqual(body['allowed_mentions']['parse'], [])

    def test_missing_discord_id_falls_back_to_the_display_name(self):
        race = _race(_racer('SirLinkalot', None), _racer('Windfox470', '222'))
        body = build_announcement(race, ROOM)

        self.assertIn('SirLinkalot', body['content'])
        self.assertNotIn('<@None>', body['content'])
        self.assertNotIn('<@>', body['content'])
        self.assertEqual(body['allowed_mentions']['users'], ['222'])

    def test_no_discord_ids_at_all_still_produces_a_post(self):
        race = _race(_racer('SirLinkalot', None), _racer('Windfox470', None))
        body = build_announcement(race, ROOM)

        self.assertEqual(body['allowed_mentions']['users'], [])
        self.assertIn(ROOM, body['content'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_announce.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ttpbot.league.announce'`

- [ ] **Step 3: Write minimal implementation**

Create `ttpbot/league/announce.py`:

```python
"""Announce League rooms to Discord.

Message content is assembled from a live spreadsheet, so mentions are
allow-listed rather than parsed: only the two racers' ids can ping.
"""

import asyncio

import aiohttp


def _mention(racer):
    if racer.discord_id:
        return '<@{}>'.format(racer.discord_id)
    return racer.display_name


def build_announcement(race, race_url):
    """Return the webhook JSON body for a League room."""
    racers = (race.runner_one, race.runner_two)
    content = 'League: {} vs {} — {}'.format(
        _mention(race.runner_one), _mention(race.runner_two), race_url,
    )
    return {
        'content': content,
        'allowed_mentions': {
            'parse': [],
            'users': [r.discord_id for r in racers if r.discord_id],
        },
    }


async def send_league_announcement(race, race_url, webhook_url, logger):
    """Post the League announcement. Returns True when Discord accepted it."""
    if not webhook_url:
        logger.warning('League Discord announcements are not configured')
        return False
    body = build_announcement(race, race_url)
    logger.info('Announcing League room: %s', race.title)
    try:
        async with aiohttp.request(
            method='post',
            url=webhook_url,
            json=body,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status in (200, 204):
                return True
            logger.error('League webhook failed (HTTP %d)', response.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, TypeError) as exc:
        logger.error('League webhook failed safely (%s)', type(exc).__name__)
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_announce.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/league/announce.py tests/test_league_announce.py
git commit -m "feat: announce League rooms with allow-listed racer mentions"
```

---

### Task 8: League scheduler loop

**Files:**
- Create: `ttpbot/league/scheduler.py`
- Test: `tests/test_league_scheduler.py`

**Interfaces:**
- Consumes: everything from Tasks 1, 2, 6, 7, plus `DestinationStateStore` and `UNCERTAIN_RACE`.
- Produces: `ScheduleSource(url, roster, logger)` with `async races(now) -> list[LeagueRace]`; `LeagueScheduler(bot, source, created_store, webhook_store, webhook_url, logger)` — all keyword arguments — with `async tick(now)` and `async run()`; `DEFAULT_SCHEDULE_URL`; constants `LEAGUE_ROOM_OPEN_MINUTES_BEFORE = 30`, `SCHEDULE_CACHE_MAX_AGE = timedelta(hours=6)`.

The scheduler takes no `roster` of its own: the roster reaches it inside `ScheduleSource`, which is the only thing that needs to resolve names.

**Cache rule:** on fetch failure, reuse the last good snapshot for up to 6 hours, then stop creating rooms. A transient Google outage must not cost a race; a sheet that has silently stopped being reachable must not keep opening rooms.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_scheduler.py`:

```python
import logging
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from ttpbot.config import TIMEZONE
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace
from ttpbot.league.scheduler import (
    LEAGUE_ROOM_OPEN_MINUTES_BEFORE,
    LeagueScheduler,
)
from ttpbot.state import UNCERTAIN_RACE, DestinationStateStore

DESTINATION = 'https://racetime.gg|z1r'
ROOM = 'https://racetime.gg/z1r/clever-slug-1234'
START = datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE)

QUIET = logging.getLogger('test-league-scheduler')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


def _racer(name, rid, did):
    return Racer(sheet_name=name, team='SC', team_full='Shadow Cartel',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id=rid, discord_id=did)


RACE = LeagueRace(start=START,
                  runner_one=_racer('SirLinkalot', 'rt-sir', '111'),
                  runner_two=_racer('Windfox470', 'rt-wind', '222'),
                  channel=None)
OTHER = LeagueRace(start=START,
                   runner_one=_racer('seanfreston', 'rt-sean', '333'),
                   runner_two=_racer('Stags28', 'rt-stags', '444'),
                   channel=None)


class FakeSource:
    def __init__(self, races):
        self._races = races
        self.calls = 0

    async def races(self, now):
        self.calls += 1
        return list(self._races)


class FakeBot:
    def __init__(self):
        self.state = {}
        self.access_token = 'token'
        self.provider = object()


class LeagueSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.created = DestinationStateStore(
            'league_races.json', DESTINATION, 'league_created_races', data_dir=root)
        self.webhooks = DestinationStateStore(
            'league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)
        self.bot = FakeBot()

    def scheduler(self, races=(RACE,)):
        return LeagueScheduler(
            bot=self.bot, source=FakeSource(list(races)),
            created_store=self.created, webhook_store=self.webhooks,
            webhook_url='https://discord.com/api/webhooks/1/token',
            logger=QUIET,
        )

    async def test_opens_a_room_inside_the_window(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create, \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=LEAGUE_ROOM_OPEN_MINUTES_BEFORE))

        create.assert_awaited_once()
        self.assertEqual(self.created.load()[RACE.key], ROOM)

    async def test_does_not_open_a_room_too_early(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create:
            await scheduler.tick(START - timedelta(minutes=45))

        create.assert_not_awaited()
        self.assertEqual(self.created.load(), {})

    async def test_does_not_reopen_a_room_on_the_next_tick(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create, \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))
            await scheduler.tick(START - timedelta(minutes=29))

        self.assertEqual(create.await_count, 1)

    async def test_opens_both_rooms_for_two_races_in_one_slot(self):
        scheduler = self.scheduler([RACE, OTHER])
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)) as create, \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(create.await_count, 2)
        self.assertEqual(len(self.created.load()), 2)

    async def test_seeds_handler_state_with_both_racers(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(
            self.bot.state['z1r/clever-slug-1234']['league_race']['invite'],
            ['rt-sir', 'rt-wind'],
        )

    async def test_announces_once(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=ROOM)), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)) as announce:
            await scheduler.tick(START - timedelta(minutes=30))
            await scheduler.tick(START - timedelta(minutes=29))

        self.assertEqual(announce.await_count, 1)
        self.assertIn(RACE.key, self.webhooks.load())

    async def test_uncertain_creation_is_recorded_and_not_announced(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=UNCERTAIN_RACE)), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)) as announce:
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(self.created.load()[RACE.key], UNCERTAIN_RACE)
        announce.assert_not_awaited()

    async def test_failed_creation_writes_no_state(self):
        scheduler = self.scheduler()
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(return_value=None)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(self.created.load(), {})

    async def test_a_race_error_does_not_stop_the_other_race(self):
        scheduler = self.scheduler([RACE, OTHER])
        with patch('ttpbot.league.scheduler.create_league_room',
                   AsyncMock(side_effect=[RuntimeError('boom'), ROOM])), \
             patch('ttpbot.league.scheduler.send_league_announcement',
                   AsyncMock(return_value=True)):
            await scheduler.tick(START - timedelta(minutes=30))

        self.assertEqual(len(self.created.load()), 1)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_scheduler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ttpbot.league.scheduler'`

- [ ] **Step 3: Write minimal implementation**

Create `ttpbot/league/scheduler.py`:

```python
"""The League scheduling loop.

Runs alongside TTPBot's own scheduler as a separate task with separate
state, so a League failure cannot affect TTP scheduling.
"""

import asyncio
from datetime import timedelta
from urllib.parse import urlsplit

import aiohttp

from ..state import UNCERTAIN_RACE
from .announce import send_league_announcement
from .rooms import create_league_room
from .schedule import parse_schedule

LEAGUE_ROOM_OPEN_MINUTES_BEFORE = 30
LEAGUE_START_BUFFER_MINUTES = 5
SCHEDULE_CACHE_MAX_AGE = timedelta(hours=6)
STATE_RETENTION = timedelta(hours=2)
TICK_SECONDS = 60
DEFAULT_SCHEDULE_URL = (
    'https://docs.google.com/spreadsheets/d/'
    '1MEyO03Wib6iyH7-75e-orh2K75AATwEoJB9HlTe9VgM/export'
    '?format=csv&gid=2033319762'
)


class ScheduleSource:
    """Fetch and cache the League schedule CSV."""

    def __init__(self, url, roster, logger):
        self.url = url
        self.roster = roster
        self.logger = logger
        self._races = []
        self._fetched_at = None

    async def races(self, now):
        try:
            async with aiohttp.request(
                method='get', url=self.url,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status != 200:
                    raise aiohttp.ClientError(
                        'HTTP {}'.format(response.status))
                body = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            return self._stale(now, exc)
        self._races = parse_schedule(body, self.roster, self.logger)
        self._fetched_at = now
        return list(self._races)

    def _stale(self, now, exc):
        """Reuse the last good snapshot, but not indefinitely."""
        if self._fetched_at is None:
            self.logger.error(
                'League schedule unavailable and no cached copy (%s)',
                type(exc).__name__)
            return []
        age = now - self._fetched_at
        if age > SCHEDULE_CACHE_MAX_AGE:
            self.logger.error(
                'League schedule stale by %s; opening no rooms', age)
            return []
        self.logger.warning(
            'League schedule fetch failed (%s); using cached copy %s old',
            type(exc).__name__, age)
        return list(self._races)


def _race_name(room_url):
    """Return the racetime race name ('z1r/slug') for a room URL."""
    return urlsplit(room_url).path.strip('/')


class LeagueScheduler:
    def __init__(self, bot, source, created_store, webhook_store,
                 webhook_url, logger):
        self.bot = bot
        self.source = source
        self.created_store = created_store
        self.webhook_store = webhook_store
        self.webhook_url = webhook_url
        self.logger = logger
        self.created = created_store.load()
        self.announced = set(webhook_store.load())

    async def run(self):
        """Tick forever. Never let a League failure kill the process."""
        while True:
            try:
                await self.tick(self._now())
            except Exception:
                self.logger.error('Error in League scheduler', exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    @staticmethod
    def _now():
        from datetime import datetime

        from ..config import TIMEZONE
        return datetime.now(TIMEZONE)

    def _prune(self, now):
        cutoff = now - STATE_RETENTION
        retained = self.created_store.cleanup_before(cutoff)
        if retained != self.created:
            self.created = retained
        webhooks = self.webhook_store.cleanup_before(cutoff)
        if set(webhooks) != self.announced:
            self.announced = set(webhooks)

    async def tick(self, now):
        self._prune(now)
        for race in await self.source.races(now):
            try:
                await self._handle(race, now)
            except Exception:
                self.logger.error('Error handling League race %s',
                                  race.title, exc_info=True)

    async def _handle(self, race, now):
        minutes_until = (race.start - now).total_seconds() / 60
        if minutes_until > LEAGUE_ROOM_OPEN_MINUTES_BEFORE:
            return
        if minutes_until < -LEAGUE_START_BUFFER_MINUTES:
            return

        room_url = self.created.get(race.key)
        if room_url is None:
            room_url = await create_league_room(
                race, self.bot.provider, self.bot.access_token, self.logger)
            if not room_url:
                return
            self.created[race.key] = room_url
            self.created_store.save(self.created)
            if room_url != UNCERTAIN_RACE:
                self._seed_handler_state(race, room_url)

        if room_url == UNCERTAIN_RACE or race.key in self.announced:
            return
        await send_league_announcement(race, room_url, self.webhook_url, self.logger)
        self.announced.add(race.key)
        self.webhook_store.save({key: True for key in self.announced})

    def _seed_handler_state(self, race, room_url):
        """Tell the handler who to invite before it exists.

        Bot.create_handler only creates state[race_name] when absent, and
        passes it into the handler by reference, so seeding it here reaches
        the handler untouched.
        """
        race_name = _race_name(room_url)
        if not race_name:
            return
        entry = self.bot.state.setdefault(race_name, {})
        entry['league_race'] = {
            'invite': [race.runner_one.racetime_id, race.runner_two.racetime_id],
            'title': race.title,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_scheduler.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/league/scheduler.py tests/test_league_scheduler.py
git commit -m "feat: add the League scheduling loop"
```

---

### Task 9: Configuration and wiring

**Files:**
- Modify: `ttpbot/runtime_config.py`
- Modify: `ttpbot/__init__.py`
- Modify: `ttpbot/bot.py` (`__init__` and `run` only)
- Modify: `deploy/ttpbot.env.example`
- Test: `tests/test_league_config.py`

**Interfaces:**
- Consumes: `LeagueScheduler`, `ScheduleSource`, `DEFAULT_SCHEDULE_URL` (Task 8); `load_roster` (Task 1).
- Produces: `BotRuntimeConfig.league_enabled: bool`, `.league_schedule_url: str`, `.league_discord_webhook_url: str | None`, and the `.league_announcements_enabled` property; `TTPBot(..., league_enabled=False, league_schedule_url=None, league_discord_webhook_url=None)`; `TTPBot._build_league_scheduler() -> LeagueScheduler | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_config.py`:

```python
import argparse
import unittest

from ttpbot.runtime_config import RuntimeConfigurationError, resolve_bot_config

WEBHOOK = 'https://discord.com/api/webhooks/123/abcdef'
BASE = {
    'TTPBOT_RACETIME_ORIGIN': 'https://racetime.gg',
    'TTPBOT_CATEGORY_SLUG': 'z1r',
    'TTPBOT_RACETIME_CLIENT_ID': 'id',
    'TTPBOT_RACETIME_CLIENT_SECRET': 'secret',
    'TTPBOT_DATA_DIR': '/var/lib/ttpbot',
    'TTPBOT_ENVIRONMENT': 'production',
}


def config(**overrides):
    env = dict(BASE)
    env.update(overrides)
    return resolve_bot_config(argparse.Namespace(), env=env)


class LeagueConfigTests(unittest.TestCase):
    def test_league_is_disabled_by_default(self):
        self.assertFalse(config().league_enabled)

    def test_league_can_be_enabled(self):
        cfg = config(TTPBOT_LEAGUE_ENABLED='true',
                     TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL=WEBHOOK)
        self.assertTrue(cfg.league_enabled)

    def test_schedule_url_has_a_default(self):
        self.assertIn('docs.google.com', config().league_schedule_url)

    def test_schedule_url_can_be_overridden(self):
        cfg = config(TTPBOT_LEAGUE_SCHEDULE_URL='https://example.com/s.csv')
        self.assertEqual(cfg.league_schedule_url, 'https://example.com/s.csv')

    def test_league_webhook_is_validated(self):
        with self.assertRaises(RuntimeConfigurationError):
            config(TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL='https://evil.example/hook')

    def test_league_webhook_is_independent_of_the_ttp_webhook(self):
        # TTP's webhook+role pairing rule must not apply to League.
        cfg = config(TTPBOT_LEAGUE_ENABLED='true',
                     TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL=WEBHOOK)
        self.assertIsNone(cfg.discord_webhook_url)
        self.assertEqual(cfg.league_discord_webhook_url, WEBHOOK)

    def test_enabled_without_a_webhook_is_allowed(self):
        # Rooms and invites still run; only the announcement is skipped.
        cfg = config(TTPBOT_LEAGUE_ENABLED='true')
        self.assertTrue(cfg.league_enabled)
        self.assertIsNone(cfg.league_discord_webhook_url)

    def test_invalid_enabled_value_is_rejected(self):
        with self.assertRaises(RuntimeConfigurationError):
            config(TTPBOT_LEAGUE_ENABLED='sometimes')

    def test_ttp_config_is_unchanged_when_league_is_off(self):
        cfg = config()
        self.assertEqual(cfg.category_slug, 'z1r')
        self.assertEqual(cfg.environment, 'production')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_config.py -v`
Expected: FAIL — `AttributeError: 'BotRuntimeConfig' object has no attribute 'league_enabled'`

- [ ] **Step 3: Write minimal implementation**

In `ttpbot/runtime_config.py`, add three fields to `BotRuntimeConfig` after `data_dir`:

```python
    league_enabled: bool = False
    league_schedule_url: Optional[str] = None
    league_discord_webhook_url: Optional[str] = None
```

Note `environment: str` has no default, so move the three new fields **after** `environment` to keep dataclass field ordering valid. The declaration order becomes: `provider, client_id, client_secret, discord_webhook_url, race_seekers_role_id, data_dir, environment, league_enabled, league_schedule_url, league_discord_webhook_url, _origin, _category`.

Add this property to `BotRuntimeConfig`:

```python
    @property
    def league_announcements_enabled(self):
        return bool(self.league_enabled and self.league_discord_webhook_url)
```

In `resolve_bot_config`, before the `return BotRuntimeConfig(...)`, add:

```python
    from .league.scheduler import DEFAULT_SCHEDULE_URL

    league_enabled = _boolean(
        _arg_or_env(args, "league_enabled", source, "TTPBOT_LEAGUE_ENABLED"),
        "TTPBOT_LEAGUE_ENABLED",
    )
    league_schedule_url = (
        _arg_or_env(args, "league_schedule_url", source, "TTPBOT_LEAGUE_SCHEDULE_URL")
        or DEFAULT_SCHEDULE_URL
    )
    league_webhook = _validate_webhook(
        _arg_or_env(args, "league_discord_webhook_url", source,
                    "TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL")
    )
```

and pass them into the `BotRuntimeConfig(...)` call:

```python
        league_enabled=league_enabled,
        league_schedule_url=league_schedule_url,
        league_discord_webhook_url=league_webhook,
```

In `ttpbot/bot.py`, extend `TTPBot.__init__` — add the three keyword arguments and store them before `super().__init__`:

```python
    def __init__(self, *args, provider=None, discord_webhook_url=None,
                 race_seekers_role_id=None, data_dir=None,
                 created_race_store=None, sent_webhook_store=None,
                 league_enabled=False, league_schedule_url=None,
                 league_discord_webhook_url=None, **kwargs):
        self.provider = provider
        self.discord_webhook_url = discord_webhook_url
        self.race_seekers_role_id = race_seekers_role_id
        self.league_enabled = league_enabled
        self.league_schedule_url = league_schedule_url
        self.league_discord_webhook_url = league_discord_webhook_url
        self.data_dir = data_dir
```

(keep the rest of `__init__` exactly as it is)

Add this method to `TTPBot`:

```python
    def _build_league_scheduler(self):
        """Construct the League scheduler, or None if it cannot start."""
        from .league.roster import RosterError, load_roster
        from .league.scheduler import LeagueScheduler, ScheduleSource

        try:
            roster = load_roster()
        except RosterError:
            self.logger.error('League roster is unusable; League scheduling is off')
            return None
        root = self.data_dir
        created = DestinationStateStore(
            'league_races.json', self.provider.destination_key,
            'league_created_races', data_dir=root)
        webhooks = DestinationStateStore(
            'league_webhooks.json', self.provider.destination_key,
            'league_sent_webhooks', data_dir=root)
        source = ScheduleSource(self.league_schedule_url, roster, self.logger)
        return LeagueScheduler(
            bot=self, source=source, created_store=created,
            webhook_store=webhooks,
            webhook_url=self.league_discord_webhook_url, logger=self.logger)
```

Extend `run()` with one additional task:

```python
    def run(self):
        """Add the race scheduler task alongside the standard bot tasks."""
        self.loop.create_task(self.reauthorize())
        self.loop.create_task(self.refresh_races())
        self.loop.create_task(self.race_scheduler())
        if self.league_enabled:
            scheduler = self._build_league_scheduler()
            if scheduler is not None:
                self.logger.info('League scheduling is enabled')
                self.loop.create_task(scheduler.run())
        self.loop.set_exception_handler(self.handle_exception)
        self.loop.run_forever()
```

Note: `build_state_stores` already resolves `data_dir` when it is `None`; `_build_league_scheduler` passes `self.data_dir` straight through, and `DestinationStateStore` treats `None` as "use the declared path's parent". To keep both consistent, set `self.data_dir = data_dir or configured_data_dir()` in `__init__` instead of the bare assignment above.

In `ttpbot/__init__.py`, add three CLI flags next to the existing ones:

```python
    parser.add_argument('--league-enabled', default=None,
                        help='enable Z1RR League scheduling (default off)')
    parser.add_argument('--league-schedule-url')
    parser.add_argument('--league-discord-webhook-url')
```

and pass the resolved values into the `TTPBot(...)` construction:

```python
        league_enabled=config.league_enabled,
        league_schedule_url=config.league_schedule_url,
        league_discord_webhook_url=config.league_discord_webhook_url,
```

Also extend the `--check-config` output line so League state is visible without network access:

```python
        print('TTPBOT_CONFIG=PASS destination={} league={}'.format(
            config.provider.destination_key,
            'on' if config.league_enabled else 'off'))
```

In `deploy/ttpbot.env.example`, append:

```sh
# Z1RR League race automation (Phase 1). Off by default.
TTPBOT_LEAGUE_ENABLED=false
# Optional: override the published League schedule CSV.
#TTPBOT_LEAGUE_SCHEDULE_URL=
# Discord webhook for #league-scheduling announcements.
#TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL=
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_config.py tests/test_runtime_config.py tests/test_deploy_contract.py -v`
Expected: PASS. The existing config and deploy tests must pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/runtime_config.py ttpbot/__init__.py ttpbot/bot.py deploy/ttpbot.env.example tests/test_league_config.py
git commit -m "feat: wire League scheduling behind a default-off flag"
```

---

### Task 10: Full-suite regression and TTP isolation proof

**Files:**
- Test: `tests/test_league_isolation.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing; this task is the guard that Phase 1 kept its central promise.

- [ ] **Step 1: Write the failing test**

Create `tests/test_league_isolation.py`:

```python
import logging
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

QUIET = logging.getLogger('test-league-isolation')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


class _FakeLoop:
    """Records tasks instead of running them."""

    def __init__(self):
        self.tasks = []

    def create_task(self, task):
        self.tasks.append(task)
        return task

    def set_exception_handler(self, handler):
        self.handler = handler

    def run_forever(self):
        self.ran = True


class _FakeScheduler:
    # A plain method, not a coroutine: an un-awaited coroutine would emit a
    # RuntimeWarning and the suite's output must stay pristine.
    def run(self):
        return 'league_scheduler'


def _bot_with_fake_loop(league_enabled):
    """A TTPBot whose loop and long-running tasks are inert stubs."""
    bot = object.__new__(bot_module.TTPBot)
    loop = _FakeLoop()
    bot.loop = loop
    bot.logger = QUIET
    bot.league_enabled = league_enabled
    bot.build_calls = 0

    def _build():
        bot.build_calls += 1
        return _FakeScheduler()

    bot._build_league_scheduler = _build
    bot.reauthorize = lambda: 'reauthorize'
    bot.refresh_races = lambda: 'refresh_races'
    bot.race_scheduler = lambda: 'race_scheduler'
    bot.handle_exception = lambda active_loop, context: None
    return bot, loop


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

    def test_run_starts_no_league_task_when_league_is_disabled(self):
        bot, loop = _bot_with_fake_loop(league_enabled=False)

        bot.run()

        self.assertEqual(bot.build_calls, 0)
        self.assertEqual(len(loop.tasks), 3)

    def test_run_starts_the_league_task_when_league_is_enabled(self):
        bot, loop = _bot_with_fake_loop(league_enabled=True)

        bot.run()

        self.assertEqual(bot.build_calls, 1)
        self.assertEqual(len(loop.tasks), 4)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_league_isolation.py -v`
Expected: PASS if Tasks 1-9 are correct. If any assertion fails, a previous task changed TTP behavior — fix that task rather than this test.

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest -v`
Expected: every pre-existing test passes unchanged, plus the new League tests.

- [ ] **Step 4: Verify configuration loads without network access**

Run:

```bash
TTPBOT_RACETIME_CLIENT_ID=id TTPBOT_RACETIME_CLIENT_SECRET=secret TTPBOT_DATA_DIR=. uv run python -m ttpbot --origin https://racetime.gg --category z1r --environment test --check-config
```

Expected: `TTPBOT_CONFIG=PASS destination=https://racetime.gg|z1r league=off`

- [ ] **Step 5: Commit**

```bash
git add tests/test_league_isolation.py
git commit -m "test: prove League scheduling leaves TTP untouched"
```

---

## Deployment note (not a task)

Deploying is `git pull --ff-only` in `/opt/ttpbot` on the coop-relay VM followed by `sudo systemctl restart ttpbot`, so the branch must be merged and pushed first. Because `TTPBOT_LEAGUE_ENABLED` defaults off, that deploy changes nothing observable. Switching League on is a separate, deliberate step: add `TTPBOT_LEAGUE_ENABLED=true` and `TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL=...` to `/etc/ttpbot.env`, then restart. Verify with `journalctl -u ttpbot -f` that `League scheduling is enabled` appears and that the first room opens 30 minutes before a scheduled match.
