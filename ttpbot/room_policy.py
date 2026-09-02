from .config import (
    GOAL_NAME,
    LEAGUE_ROOM_INFO_PREFIX,
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


def is_league_room(race_data):
    """Return True for Z1RR League rooms this bot scheduled.

    League rooms share the 'Beat the game' goal with TTP post-season rooms,
    so the info_bot prefix is what separates them. The prefix is one this
    automation writes itself, so a community room cannot match.
    """
    goal_name = race_data.get('goal', {}).get('name', '')
    if goal_name != POST_SEASON_GOAL_NAME:
        return False
    info_bot = race_data.get('info_bot', '') or ''
    return info_bot.startswith(LEAGUE_ROOM_INFO_PREFIX)
