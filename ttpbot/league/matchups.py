"""The League fixture list: who is away, who is home, and in which week.

The Schedule tab cannot answer this. Its Runner 1 and Runner 2 columns are
filled in by whoever completed the form, so their order carries no meaning -
for the 2026-09-06 race the Schedule reads "(MiB) cUstOm" then "(TUG) OzzSolo"
while the fixture is Three Unique Gamers away at Midwest is Best. Taking slot
order from the Schedule would put the teams on the wrong sides of the canvas
about half the time.

The Matchups tab is the authority, laid out as repeating blocks:

    "Week 1 - TTP3 Wisdom: <flag string>","",""
    "Division","Away Team","Home Team"
    "Hyrule Division","Fahrenheit 451","Shadow Cartel"
    ...

A pair of teams identifies exactly one fixture. Verified against the live tab
on 2026-09-06: 8 weeks and 51 rows produce 50 distinct pairings, the only
repeat being TBD vs TBD in week 8. So no date-to-week mapping is needed, and a
race played late still carries the week number of its fixture rather than of
the day it was played.
"""

import csv
from dataclasses import dataclass
import io
import re
from typing import Optional

WEEK_HEADER = re.compile(r'^Week\s+(\d+)\b', re.IGNORECASE)
NON_ALNUM = re.compile(r'[^a-z0-9]+')

#: Placeholders for fixtures not yet decided. They are also the only repeated
#: pairing in the sheet, so admitting them would manufacture an ambiguity.
PLACEHOLDERS = {'tbd', ''}

#: The column header that sits under every week header.
COLUMN_HEADER = {'division', 'awayteam', 'hometeam'}


def _key(value: str) -> str:
    """Compare on letters and digits only.

    The two tabs do not spell teams identically - roster.json has
    "Midwest Is Best" and "The Missing Links" where the sheet has
    "Midwest is Best" and "The missing links", and LKU differs by commas.
    """
    return NON_ALNUM.sub('', (value or '').lower())


@dataclass(frozen=True)
class Fixture:
    week: int
    away: str
    home: str


class Matchups:
    """Team pairings resolved to a fixture, in either order."""

    def __init__(self, by_pair):
        self._by_pair = by_pair

    @property
    def count(self) -> int:
        return len(self._by_pair)

    def fixture_for(self, team_a: str, team_b: str) -> Optional[Fixture]:
        """The one fixture these two teams play, or None.

        None for an unscheduled pairing and None for an ambiguous one: putting
        the teams on the wrong sides is worse than declining to say.
        """
        return self._by_pair.get(frozenset((_key(team_a), _key(team_b))))


def parse_matchups(csv_text: str, logger) -> Matchups:
    """Read the Matchups tab. Total: a row it cannot use is skipped."""
    by_pair = {}
    ambiguous = set()
    week = None

    try:
        rows = list(csv.reader(io.StringIO(csv_text)))
    except csv.Error as exc:
        logger.warning('League matchups could not be parsed: %s', exc)
        return Matchups({})

    for row in rows:
        if not row or not (row[0] or '').strip():
            continue

        header = WEEK_HEADER.match(row[0].strip())
        if header:
            week = int(header.group(1))
            continue

        if len(row) < 3:
            continue
        away, home = (row[1] or '').strip(), (row[2] or '').strip()
        away_key, home_key = _key(away), _key(home)

        # The column header repeats under every week header.
        if {_key(row[0]), away_key, home_key} == COLUMN_HEADER:
            continue
        if away_key in PLACEHOLDERS or home_key in PLACEHOLDERS:
            continue
        if week is None:
            # Fixtures before any week header have no week to belong to.
            logger.warning('League matchup %r skipped: no week header above it', row)
            continue

        pair = frozenset((away_key, home_key))
        if pair in by_pair:
            # Home and away reversed is still the same two teams. Choosing one
            # would be guessing which side each belongs on.
            logger.warning(
                'League matchup %s vs %s appears more than once; both ignored', away, home,
            )
            ambiguous.add(pair)
            continue
        by_pair[pair] = Fixture(week=week, away=away, home=home)

    for pair in ambiguous:
        by_pair.pop(pair, None)
    return Matchups(by_pair)
