"""The Autumn Tournament's Schedule tab, as rows.

Deliberately thinner than the League's parser, because this one does not know
who a racer is. A League row names people on a roster with teams; an Autumn row
names two strings a racer picked from a form's dropdown, and which *match* that
is cannot be answered without the bracket. So this stops at "two names and a
time", and `matching.py` takes it from there.

The tab, as it actually reads today:

    Date,Time,Runner 1,Runner 2,,Comms 1,Comms 2,Tracker,,Channel

Two blank spacer columns, no `Game` -- Autumn is one game per round -- and the
crew columns are the restream's, not the race's.

Three things it does not do, each on purpose:

  * it does not resolve a name. The dropdowns carry the tournament ranking as a
    prefix and the sheet's spelling of somebody may not be the draw's, and both
    of those are the matcher's business.
  * it does not deduplicate. A reschedule appears as a *second row* for the same
    pair, which is exactly what a scheduled grand-final reset looks like too.
    Collapsing them here would throw away the only evidence that tells them
    apart, which is their order in time.
  * it does not skip a row it cannot fully read. A row with an unreadable time is
    reported, because two racers who think they are scheduled deserve better than
    silence.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..schedule_sheet import (
    REQUIRED_COLUMNS,
    cell,
    header_is_readable,
    parse_start,
    resolve_columns,
    rows_of,
)

#: Whether a response is the Schedule tab at all, rather than a sign-in page.
#: The distinction a runner has to make before it believes an empty schedule.
schedule_is_readable = header_is_readable


@dataclass(frozen=True)
class ScheduleRow:
    """One row of the tab, read but not interpreted.

    `at` is the only cell that is parsed. The names are carried exactly as the
    sheet spells them, prefix and all, because the matcher needs the raw text to
    apply the engine's aliases to -- stripping anything here would be a second
    opinion about who somebody is.
    """

    at: datetime
    runner_one: str
    runner_two: str
    comms_one: str = ''
    comms_two: str = ''
    tracker: str = ''
    channel: str = ''
    #: 1-based line in the tab, for saying *which* row when something is wrong.
    line: int = 0

    @property
    def crew(self):
        """Whoever is on the restream, in the order the sheet lists them."""
        return [name for name in (self.comms_one, self.comms_two, self.tracker) if name]

    def as_row(self):
        """The shape `matching.match_rows` takes."""
        return {
            'at': self.at,
            'runner_one': self.runner_one,
            'runner_two': self.runner_two,
        }


@dataclass(frozen=True)
class BadRow:
    """A row that could not be read, and why.

    Its own type because these must be *said*. A row silently skipped is two
    racers with a time they agreed and no room at it, and the first anyone hears
    of it is on the night.
    """

    line: int
    reason: str
    cells: tuple = ()


@dataclass
class Schedule:
    rows: list = field(default_factory=list)
    bad: list = field(default_factory=list)
    #: False when the response was not the tab -- a sign-in page, an error
    #: document, anything without the header. Distinct from "no rows", which is
    #: a legitimately empty schedule and means every race is done or cancelled.
    readable: bool = True

    def in_time_order(self):
        """The rows earliest first, which is what separates the two finals.

        Two rows for the finalists are the final and its reset, and the only
        thing that says which is which is that one is before the other.
        """
        return sorted(self.rows, key=lambda row: row.at)


def parse_schedule(csv_text, logger=None):
    """Every row of the Schedule tab, with the ones that could not be read.

    Total. A tab whose header is unusable comes back `readable=False` with no
    rows rather than parsed against guessed positions: reading the wrong columns
    is how a race gets built from the wrong cells, and no room beats a wrong one.
    """
    rows = rows_of(csv_text)
    if not rows:
        if logger:
            logger.warning('Autumn schedule response was empty')
        return Schedule(readable=False)

    columns = resolve_columns(rows[0])
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        if logger:
            logger.warning(
                'Autumn schedule header is unusable, missing %s; found %r',
                ', '.join(missing), rows[0],
            )
        return Schedule(readable=False)

    found = Schedule()
    for line, row in enumerate(rows[1:], start=2):
        date_text = cell(row, columns, 'date')
        time_text = cell(row, columns, 'time')
        one = cell(row, columns, 'runner_one')
        two = cell(row, columns, 'runner_two')

        # A wholly blank line is the trailing newline of a spreadsheet export,
        # and saying something about it every minute would bury the rows that
        # matter.
        if not any((date_text, time_text, one, two)):
            continue

        if not (one and two):
            found.bad.append(BadRow(line, 'a row needs both runners', tuple(row)))
            continue
        try:
            at = parse_start(date_text, time_text)
        except ValueError as exc:
            found.bad.append(BadRow(line, str(exc), tuple(row)))
            continue
        except Exception as exc:  # noqa: BLE001 - a cell can hold anything
            found.bad.append(
                BadRow(line, 'unreadable date or time: {}'.format(exc), tuple(row)))
            continue

        found.rows.append(ScheduleRow(
            at=at,
            runner_one=one,
            runner_two=two,
            comms_one=cell(row, columns, 'comms_one'),
            comms_two=cell(row, columns, 'comms_two'),
            tracker=cell(row, columns, 'tracker'),
            channel=cell(row, columns, 'channel'),
            line=line,
        ))

    if found.bad and logger:
        for bad in found.bad:
            logger.warning('Autumn schedule row %d: %s', bad.line, bad.reason)
    return found
