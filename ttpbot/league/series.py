"""Championship Week series games, recognised from the Schedule tab.

A series game is two rows at one time, like co-op, but all four racers race
together on one broadcast. The finalists also met in the regular season, so
the Matchups tab's fixture for that pairing is the wrong week; from the week's
opening on, a finalist pairing is its series (scheduling_threads.SERIES).
"""

from .matchups import Fixture, _key
from .scheduling_threads import SERIES, open_at


def series_fixture_for(team_a, team_b, start):
    """The series these two teams play at `start`, or None."""
    wanted = {_key(team_a), _key(team_b)}
    for week, series in SERIES.items():
        opens = open_at(week)
        if opens is None or start < opens:
            continue
        for one in series:
            if {_key(one.higher), _key(one.lower)} == wanted:
                # Lower seed as `away`, as the threads name them: `lower @ higher`.
                return Fixture(week=week, away=one.lower, home=one.higher, label=one.name, series=True)
    return None
