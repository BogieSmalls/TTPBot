from .config import (
    GOAL_NAME,
    AUTUMN_ROOM_INFO_PREFIX, LEAGUE_ROOM_INFO_PREFIX,
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


def is_autumn_room(race_data):
    """Return True for Autumn Tournament rooms this bot scheduled.

    The same shape of test as `is_league_room`, against a different prefix. Both
    share the 'Beat the game' goal with TTP post-season rooms, so the prefix is
    what separates all three -- and because this automation writes it, a
    community room cannot match.
    """
    goal_name = race_data.get('goal', {}).get('name', '')
    if goal_name != POST_SEASON_GOAL_NAME:
        return False
    info_bot = race_data.get('info_bot', '') or ''
    info_user = race_data.get('info_user', '') or ''
    return (
        info_user.startswith(AUTUMN_ROOM_INFO_PREFIX)
        or info_bot.startswith(AUTUMN_ROOM_INFO_PREFIX)
    )


def is_league_room(race_data):
    """Return True for Z1RR League rooms this bot scheduled.

    League rooms share the 'Beat the game' goal with TTP post-season rooms,
    so the info_bot/info_user prefix is what separates them. The prefix is
    one this automation writes itself, so a community room cannot match.

    The title is written to both info_bot and info_user at room creation.
    Other authorised category bots (e.g. SahasrahBot rolling a seed) can
    overwrite info_bot, so info_user is checked too -- either field
    carrying the prefix is enough to recognise the room.
    """
    goal_name = race_data.get('goal', {}).get('name', '')
    if goal_name != POST_SEASON_GOAL_NAME:
        return False
    info_bot = race_data.get('info_bot', '') or ''
    info_user = race_data.get('info_user', '') or ''
    return (
        info_user.startswith(LEAGUE_ROOM_INFO_PREFIX)
        or info_bot.startswith(LEAGUE_ROOM_INFO_PREFIX)
    )
