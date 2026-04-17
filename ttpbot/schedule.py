from datetime import datetime, timedelta

from .config import SEASON_START, SEASON_END, TIMEZONE, WEEKLY_SCHEDULE


def is_within_season(dt):
    """Check if a datetime falls within the TTP S4 season."""
    return SEASON_START <= dt.date() <= SEASON_END


def get_races_for_date(d):
    """Return a list of timezone-aware datetimes for all races on calendar date d."""
    times = WEEKLY_SCHEDULE.get(d.weekday(), [])
    races = []
    for t in times:
        dt = datetime.combine(d, t, tzinfo=TIMEZONE)
        if is_within_season(dt):
            races.append(dt)
    return races


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
