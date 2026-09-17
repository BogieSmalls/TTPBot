"""Co-op League matches: two teams of two, entered as two Schedule rows.

The Schedule tab has no co-op shape. Organisers enter a co-op match as two
ordinary rows at the same time, each pairing one runner from each team, and
the Matchups tab names the week ("Week 3 - Coop Info Share ..."). Both formats
score a team by the sum of its runners' times, so racetime needs no teams:
one unranked room holding all four runners is the whole requirement.

Grouping is strict. An incomplete or inconsistent match opens nothing rather
than falling back to 1v1 rooms, because those would be ranked rooms for a race
that is not a 1v1.
"""

from dataclasses import dataclass
from datetime import timedelta

from ..config import LEAGUE_ROOM_INFO_PREFIX
from .schedule import _slugify

#: A broken match this far past its start is history, not news. Matches the
#: scheduler's LEAGUE_START_BUFFER_MINUTES (not imported: scheduler imports us).
PAST_GRACE = timedelta(minutes=5)

#: A broken match is only reported once it is this close - the scheduler's
#: LEAGUE_WAKE_MINUTES_BEFORE, when somebody can still fix the sheet in time.
WARN_AHEAD = timedelta(minutes=35)

#: The handler recovers invites after a restart by splitting the room title on
#: these, so a display name containing either would make the title ambiguous.
TITLE_SEPARATORS = (' & ', ' vs. ')


def _unique(values):
    merged = []
    for value in values:
        if value and value not in merged:
            merged.append(value)
    return tuple(merged)


@dataclass(frozen=True)
class CoopMatch:
    """One co-op match. Duck-types as a LeagueRace for the scheduler."""

    rows: tuple

    @property
    def coop(self):
        return True

    @property
    def start(self):
        return self.rows[0].start

    @property
    def fixture(self):
        return self.rows[0].fixture

    @property
    def game(self):
        return self.rows[0].game

    @property
    def away_runners(self):
        return tuple(row.away_racer for row in self.rows)

    @property
    def home_runners(self):
        return tuple(row.home_racer for row in self.rows)

    @property
    def runners(self):
        return self.away_runners + self.home_runners

    @property
    def invite_ids(self):
        return tuple(racer.racetime_id for racer in self.runners)

    @property
    def featured_rows(self):
        """Rows somebody intends to restream, one per channel.

        Two rows naming the same channel are one restream: a second booth
        request there would come back as a continuation of our own match and
        post a false already-on-air warning.
        """
        seen = set()
        featured = []
        for row in self.rows:
            if not row.channel:
                continue
            channel = row.channel.casefold()
            if channel in seen:
                continue
            seen.add(channel)
            featured.append(row)
        return tuple(featured)

    @property
    def channel(self):
        featured = self.featured_rows
        return featured[0].channel if featured else None

    @property
    def comms(self):
        return _unique(name for row in self.rows for name in row.comms)

    @property
    def trackers(self):
        """Every tracker either row names. Both are credited: silently
        picking one would drop a volunteer from the announcement."""
        return _unique(row.tracker for row in self.rows)

    @property
    def slug(self):
        return 'coop-{}-vs-{}'.format(_slugify(self.fixture.away), _slugify(self.fixture.home))

    @property
    def key(self):
        return '{}|{}'.format(self.start.isoformat(), self.slug)

    @property
    def title(self):
        return '{}{} vs. {}'.format(
            LEAGUE_ROOM_INFO_PREFIX,
            ' & '.join(racer.display_name for racer in self.away_runners),
            ' & '.join(racer.display_name for racer in self.home_runners),
        )


def _problem(rows):
    """Why these rows are not a usable co-op match, or None."""
    if len(rows) != 2:
        return 'expected 2 rows, found {}'.format(len(rows))
    games = {row.game for row in rows}
    if len(games) != 1:
        return 'rows name different games ({})'.format(
            ', '.join(str(game) for game in sorted(games, key=str)))
    for row in rows:
        if row.away_racer is None:
            return '{} does not line up with the fixture'.format(row.slug)
    racers = [racer for row in rows for racer in (row.runner_one, row.runner_two)]
    if len({racer.racetime_id for racer in racers}) != 4:
        return 'the four runners are not four different racers'
    for racer in racers:
        if any(separator in racer.display_name for separator in TITLE_SEPARATORS):
            return 'display name {!r} would make the room title unparseable'.format(
                racer.display_name)
    return None


def _worth_reporting(start, now):
    if now is None:
        return True
    return now - PAST_GRACE <= start <= now + WARN_AHEAD


def group_coop_matches(races, logger, now=None):
    """Replace each co-op week's pair of rows with one CoopMatch.

    1v1 rows pass through untouched and in order; a match takes its first
    row's place. A broken match is always held, but only reported inside the
    window where somebody can act on it.
    """
    groups = {}
    order = []
    for race in races:
        fixture = race.fixture
        if fixture is None or not fixture.coop:
            order.append(race)
            continue
        group_key = (race.start, fixture)
        if group_key not in groups:
            groups[group_key] = []
            order.append(group_key)
        groups[group_key].append(race)

    result = []
    for item in order:
        if not isinstance(item, tuple):
            result.append(item)
            continue
        start, fixture = item
        rows = groups[item]
        problem = _problem(rows)
        if problem is None:
            result.append(CoopMatch(rows=tuple(rows)))
        elif _worth_reporting(start, now):
            logger.warning(
                'League co-op match %s vs %s at %s held, no room opened: %s',
                fixture.away, fixture.home, start.isoformat(), problem,
            )
    return result
