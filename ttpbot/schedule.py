from datetime import datetime, time, timedelta

from .config import (
    POST_SEASON_GOAL_NAME,
    POST_SEASON_ROOM_INFO_PREFIX,
    TIMEZONE,
)
from .season_record import NIGHT_ROLLOVER_HOUR, last_night, seasons, slots_on_night


def _season_of_slate(night):
    """The season a slate falls inside, or None outside every season's nights."""
    season = seasons.for_night(night)
    return season if season and night <= last_night(season) else None


def is_within_season(dt):
    """Check if a race belongs to a slate inside a TTP regular season.

    Judged by slate date, not calendar date: the final Saturday's 12:00 AM
    race lands on the Sunday after the last night but still closes out the
    final slate.
    """
    return _season_of_slate(slate_date(dt.date(), dt.time())) is not None


def is_scheduled_date(d):
    """Check if the TTP schedule should produce rooms for the slate on date d.

    A season set to stop opens nothing after its last night, until the next
    season's first night.
    """
    season = seasons.for_night(d)
    if season is None:
        return False
    return season['schedule']['afterLastRace'] != 'stop' or d <= last_night(season)


def slate_date(d, t):
    """Return the date of the slate a race belongs to.

    The small-hours races close out the previous evening's slate, so they belong
    to the day before the calendar date they land on.
    """
    return d - timedelta(days=1) if t.hour < NIGHT_ROLLOVER_HOUR else d


def race_goal_for_time(scheduled_time):
    """Return the racetime.gg goal for a scheduled TTP room."""
    season = _season_of_slate(slate_date(scheduled_time.date(), scheduled_time.time()))
    return season['goal'] if season else POST_SEASON_GOAL_NAME


def race_info_for_time(scheduled_time):
    """Return the room info label for a scheduled TTP room."""
    season = _season_of_slate(slate_date(scheduled_time.date(), scheduled_time.time()))
    prefix = season['roomPrefix'] if season else POST_SEASON_ROOM_INFO_PREFIX
    formatted = scheduled_time.strftime('%a %b %d, %I:%M %p %Z')
    return f'{prefix} | Scheduled: {formatted}'


def get_races_for_date(d):
    """Return a list of timezone-aware datetimes for all races on calendar date d."""
    times = []
    # This evening's slate, then the small hours of last night's.
    for night, small_hours in ((d, False), (d - timedelta(days=1), True)):
        if not is_scheduled_date(night):
            continue
        for slot in slots_on_night(seasons.for_night(night), night):
            at = time.fromisoformat(slot['at'])
            if (at.hour < NIGHT_ROLLOVER_HOUR) == small_hours:
                times.append(at)
    return [datetime.combine(d, t, tzinfo=TIMEZONE) for t in sorted(times)]


def slot_name(scheduled_time):
    """What the community calls this race (TTP1), or None if no slot is at that time."""
    night = slate_date(scheduled_time.date(), scheduled_time.time())
    return next((slot['name'] for slot in slots_on_night(seasons.current(scheduled_time), night)
                 if time.fromisoformat(slot['at']) == scheduled_time.time().replace(tzinfo=None)), None)


def get_upcoming_races(now, window_minutes=35):
    """
    Return race times whose room-open window includes 'now'.

    A race is "upcoming" if its open time (30 min before start) has passed
    but it hasn't started yet (with a small buffer).
    """
    from .config import ROOM_OPEN_MINUTES_BEFORE

    results = []
    for delta_days in range(2):
        d = (now + timedelta(days=delta_days)).date()
        for race_time in get_races_for_date(d):
            open_time = race_time - timedelta(minutes=ROOM_OPEN_MINUTES_BEFORE)
            # Include if we're within the window: open_time <= now < race_time + buffer
            if open_time <= now < race_time + timedelta(minutes=5):
                results.append(race_time)
    return results


def get_todays_remaining_races(now):
    """Return upcoming races for today and early tomorrow, capped at next 5."""
    today = now.date()
    tomorrow = today + timedelta(days=1)
    races = get_races_for_date(today) + get_races_for_date(tomorrow)
    return [r for r in races if r > now][:5]


def find_nearest_scheduled_race(now):
    """Find the scheduled race time closest to 'now'."""
    candidates = []
    for delta_days in range(-1, 2):
        d = (now + timedelta(days=delta_days)).date()
        candidates.extend(get_races_for_date(d))

    if not candidates:
        return None

    return min(candidates, key=lambda r: abs((r - now).total_seconds()))
