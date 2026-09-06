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
NON_ALNUM = re.compile(r'[^a-z0-9]+')

MINIMUM_COLUMNS = 5

# Logical column -> the header texts that name it, lowercased.
#
# Located by header rather than by position because position has already
# moved once: splitting "Comms" into "Comms 1" and "Comms 2" pushed Channel
# from index 9 to 10, and nothing failed - the parser simply read a blank
# spacer as the channel from then on. Accepting the old spellings too means
# the sheet can be reshaped without a synchronised deploy.
COLUMN_ALIASES = {
    'date': ('date',),
    'time': ('time',),
    'runner_one': ('runner 1', 'runner one'),
    'runner_two': ('runner 2', 'runner two'),
    'comms_one': ('comms 1', 'comms one', 'comms'),
    'comms_two': ('comms 2', 'comms two'),
    'tracker': ('tracker',),
    'channel': ('channel',),
    'game': ('game',),
}

# Without these there is no race to build, so a sheet missing any of them is
# refused outright rather than parsed against guessed positions.
REQUIRED_COLUMNS = ('date', 'time', 'runner_one', 'runner_two')


def _resolve_columns(header_row):
    """Map logical column names to indices using the sheet's own header."""
    seen = {}
    for index, cell in enumerate(header_row):
        text = cell.strip().lower()
        if not text:
            continue
        for logical, aliases in COLUMN_ALIASES.items():
            # First match wins: a legacy "Comms" must not later be overwritten
            # by something that merely looks similar further right.
            if text in aliases and logical not in seen:
                seen[logical] = index
    return seen


def _cell(row, columns, logical):
    """Trimmed value of a logical column, or '' when absent for this row."""
    index = columns.get(logical)
    if index is None or index >= len(row):
        return ''
    return row[index].strip()


def _slugify(value):
    return NON_SLUG.sub('-', value.strip().lower()).strip('-')


@dataclass(frozen=True)
class LeagueRace:
    start: datetime
    runner_one: Racer
    runner_two: Racer
    channel: Optional[str] = None
    #: Raw Comms names from the sheet, in column order, blanks dropped.
    #: Left unresolved here - identity is the crew roster's job, not the
    #: schedule's, and an unresolvable name must not cost us the race.
    comms: tuple = ()
    #: Raw Tracker name from the sheet, or None.
    tracker: Optional[str] = None
    #: The game within the fixture, from the sheet's Game column.
    game: Optional[int] = None
    #: The Matchups fixture these two racers play, when it resolved.
    #:
    #: None means this race cannot be orchestrated: no week number for the
    #: title, and no way to tell which team is away. Room creation, racer
    #: invites and the Discord post are unaffected - Phase 1 never needed it.
    fixture: Optional[object] = None

    @property
    def away_racer(self):
        """The racer whose team is away, or None without a fixture."""
        if self.fixture is None:
            return None
        return (
            self.runner_one
            if _same_team(self.runner_one, self.fixture.away)
            else self.runner_two
        )

    @property
    def home_racer(self):
        if self.fixture is None:
            return None
        return self.runner_two if self.away_racer is self.runner_one else self.runner_one

    @property
    def orchestratable(self):
        """Whether a booth can be built for this race.

        A channel says somebody intends to restream it; a fixture is what
        makes the booth correct. Without one there is no week number and no
        way to tell which team belongs on which side, and guessing would put
        them on the wrong sides about half the time.
        """
        return bool(self.channel) and self.fixture is not None

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


def _same_team(racer, team_name):
    """Whether this racer plays for that team, spelled either tab's way."""
    key = NON_ALNUM.sub('', (team_name or '').lower())
    return key in {
        NON_ALNUM.sub('', (racer.team_full or '').lower()),
        NON_ALNUM.sub('', (racer.team or '').lower()),
    }


def _parse_start(date_text, time_text):
    day = datetime.strptime(date_text.strip(), DATE_FORMAT).date()
    for fmt in TIME_FORMATS:
        try:
            clock = datetime.strptime(time_text.strip().upper(), fmt).time()
        except ValueError:
            continue
        return datetime.combine(day, clock, tzinfo=TIMEZONE)
    raise ValueError('unrecognised time: {!r}'.format(time_text))


def parse_schedule(csv_text, roster, logger, matchups=None):
    """Return every well-formed, fully resolvable race in the sheet."""
    races = []
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        logger.warning('League schedule is empty')
        return races
    columns = _resolve_columns(rows[0])
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        # Never fall back to fixed positions: reading the wrong columns is how
        # a race gets built from the wrong cells, and no room beats a wrong one.
        logger.warning(
            'League schedule header is unusable, missing %s; found %r',
            ', '.join(missing), rows[0],
        )
        return races
    for number, row in enumerate(rows[1:], start=2):
        if len(row) < MINIMUM_COLUMNS:
            # A blank trailing line is normal and stays silent; a
            # populated-but-short row is the signal that the sheet's
            # column shape changed (or the response isn't the sheet at
            # all, e.g. an HTML sign-in page), so it's worth surfacing.
            if any(cell.strip() for cell in row):
                logger.warning(
                    'League row %d skipped: only %d column(s), expected at least %d',
                    number, len(row), MINIMUM_COLUMNS,
                )
            continue
        if not any(cell.strip() for cell in row):
            continue
        try:
            start = _parse_start(_cell(row, columns, 'date'), _cell(row, columns, 'time'))
        except ValueError as exc:
            logger.warning('League row %d skipped: %s', number, exc)
            continue
        try:
            one = roster.resolve(_cell(row, columns, 'runner_one'))
            two = roster.resolve(_cell(row, columns, 'runner_two'))
        except UnknownRacerError as exc:
            logger.warning('League row %d skipped: %s', number, exc)
            continue
        if one.racetime_id == two.racetime_id:
            logger.warning(
                'League row %d skipped: both runners resolve to %s',
                number, one.sheet_name,
            )
            continue
        comms = tuple(
            name for name in (
                _cell(row, columns, 'comms_one'), _cell(row, columns, 'comms_two'),
            ) if name
        )
        game_text = _cell(row, columns, 'game')
        channel = _cell(row, columns, 'channel') or None

        fixture = None
        if matchups is not None:
            fixture = matchups.fixture_for(one.team_full, two.team_full)
            if fixture is None and channel:
                # Only worth saying for a race somebody meant to restream. An
                # unresolved fixture is never guessed past: no week number and
                # no away/home means no booth.
                logger.warning(
                    'League row %d has no usable fixture for %s vs %s; '
                    'room and announcement proceed, booth does not',
                    number, one.team_full, two.team_full,
                )

        races.append(LeagueRace(
            start=start, runner_one=one, runner_two=two,
            channel=channel,
            comms=comms,
            tracker=_cell(row, columns, 'tracker') or None,
            game=int(game_text) if game_text.isdigit() else None,
            fixture=fixture,
        ))
    return races
