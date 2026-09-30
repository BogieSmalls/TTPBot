"""Reading a schedule tab, for whichever competition owns one.

The League and the Autumn Tournament submit to different tabs of the same
workbook, through forms built by the same person, and the tabs have the same
bones: a date, a time, two runners, and some crew columns beside them. What
differs is what a row *means* -- a League fixture has teams and a week, a
tournament row has to be resolved against a bracket -- and that part stays in
each competition's own package.

What lives here is the part that must not exist twice: which header names a
column, how a date and a time become an instant, and whether a response is the
sheet at all. The League's parser learned each of those the hard way, and a
second copy would get to learn them again separately.

Every cell is untrusted input. Nothing here raises on a bad row; it returns what
it could read and lets the caller decide what to say about the rest.
"""

import csv
from datetime import datetime
import io

from .config import TIMEZONE

DATE_FORMAT = '%m/%d/%Y'
TIME_FORMATS = ('%I:%M:%S %p', '%I:%M %p', '%H:%M:%S', '%H:%M')

# Logical column -> the header texts that name it, lowercased.
#
# Located by header rather than by position because position has already moved
# once: splitting "Comms" into "Comms 1" and "Comms 2" pushed Channel from index
# 9 to 10, and nothing failed -- the parser simply read a blank spacer as the
# channel from then on. Accepting the old spellings too means a tab can be
# reshaped without a synchronised deploy.
#
# The tabs carry blank spacer columns, which is the other half of that bug: the
# Autumn tab's header is
#
#     Date,Time,Runner 1,Runner 2,,Comms 1,Comms 2,Tracker,,Channel
#
# so Channel is at index 9 with two empty headers before it, and any count of
# columns is the wrong way to find anything.
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

# Without these there is no race to build, so a tab missing any of them is
# refused outright rather than parsed against guessed positions.
REQUIRED_COLUMNS = ('date', 'time', 'runner_one', 'runner_two')


def rows_of(csv_text):
    """The tab as a list of rows. Total: unparseable text is no rows."""
    return list(csv.reader(io.StringIO(csv_text or '')))


def resolve_columns(header_row):
    """Map logical column names to indices using the tab's own header."""
    seen = {}
    for index, cell_text in enumerate(header_row):
        text = cell_text.strip().lower()
        if not text:
            continue
        for logical, aliases in COLUMN_ALIASES.items():
            # First match wins: a legacy "Comms" must not later be overwritten
            # by something that merely looks similar further right.
            if text in aliases and logical not in seen:
                seen[logical] = index
    return seen


def cell(row, columns, logical):
    """Trimmed value of a logical column, or '' when absent for this row."""
    index = columns.get(logical)
    if index is None or index >= len(row):
        return ''
    return row[index].strip()


def parse_start(date_text, time_text):
    """A date cell and a time cell as one instant, in the league's timezone.

    Raises for anything it cannot read, because a race at the wrong time is
    worse than a race the caller skips and logs.
    """
    day = datetime.strptime(date_text.strip(), DATE_FORMAT).date()
    for fmt in TIME_FORMATS:
        try:
            clock = datetime.strptime(time_text.strip().upper(), fmt).time()
        except ValueError:
            continue
        return datetime.combine(day, clock, tzinfo=TIMEZONE)
    raise ValueError('unrecognised time: {!r}'.format(time_text))


def header_is_readable(csv_text):
    """Whether a response is a schedule tab at all.

    A parser returns nothing for three different situations -- no rows, a header
    it cannot use, and a usable header with no races under it -- and a caller
    cannot tell them apart from an empty list. The difference matters: a council
    member clearing the remaining races is a legitimate empty schedule, and
    Google serving an HTML sign-in page with HTTP 200 is not. Treating the second
    as the first opens no rooms; treating the first as the second keeps opening
    rooms for races that were cancelled.

    Readable means there are rows and the header names the columns a race needs,
    which is exactly what a sign-in page fails.
    """
    rows = rows_of(csv_text)
    if not rows:
        return False
    columns = resolve_columns(rows[0])
    return all(name in columns for name in REQUIRED_COLUMNS)
