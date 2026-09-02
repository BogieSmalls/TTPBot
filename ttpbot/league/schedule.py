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
