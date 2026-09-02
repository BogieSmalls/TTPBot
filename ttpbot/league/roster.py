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
