from datetime import datetime, timedelta

from .config import (
    GOAL_NAME,
    POST_SEASON_GOAL_NAME,
    POST_SEASON_ROOM_INFO_PREFIX,
    REGULAR_SEASON_ROOM_INFO_PREFIX,
    SEASON_END,
    SEASON_START,
    TIMEZONE,
    WEEKLY_SCHEDULE,
)


def is_within_season(dt):
    """Check if a datetime falls within the TTP S4 regular season."""
    return SEASON_START <= dt.date() <= SEASON_END


def is_scheduled_date(d):
    """Check if the regular weekly TTP schedule should produce rooms for date d."""
    return d >= SEASON_START


def race_goal_for_time(scheduled_time):
    """Return the racetime.gg goal for a scheduled TTP room."""
    if is_within_season(scheduled_time):
        return GOAL_NAME
    return POST_SEASON_GOAL_NAME


def race_info_for_time(scheduled_time):
    """Return the room info label for a scheduled TTP room."""
    if is_within_season(scheduled_time):
        prefix = REGULAR_SEASON_ROOM_INFO_PREFIX
    else:
        prefix = POST_SEASON_ROOM_INFO_PREFIX
    formatted = scheduled_time.strftime('%a %b %d, %I:%M %p %Z')
    return f'{prefix} | Scheduled: {formatted}'


def get_races_for_date(d):
    """Return a list of timezone-aware datetimes for all races on calendar date d."""
    if not is_scheduled_date(d):
        return []

    times = WEEKLY_SCHEDULE.get(d.weekday(), [])
    return [datetime.combine(d, t, tzinfo=TIMEZONE) for t in times]


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
