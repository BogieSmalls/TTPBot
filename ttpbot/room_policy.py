from .config import (
    GOAL_NAME,
    POST_SEASON_GOAL_NAME,
    TTP_ROOM_INFO_PREFIXES,
)


def is_ttp_scheduled_room(race_data):
    """Return True for TTP-managed rooms, including labeled post-season rooms."""
    goal_name = race_data.get('goal', {}).get('name', '')
    if goal_name == GOAL_NAME:
        return True
    if goal_name != POST_SEASON_GOAL_NAME:
        return False

    info_bot = race_data.get('info_bot', '') or ''
    return any(
        info_bot.startswith(f'{prefix} | Scheduled:')
        for prefix in TTP_ROOM_INFO_PREFIXES
    )
