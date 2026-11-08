import re

from .config import (
    AUTUMN_ROOM_INFO_PREFIX, LEAGUE_ROOM_INFO_PREFIX,
    POST_SEASON_GOAL_NAME,
    POST_SEASON_ROOM_INFO_PREFIX,
)
from .season_record import seasons


def is_ttp_scheduled_room(race_data):
    """Return True for TTP-managed rooms, including labeled post-season rooms."""
    goal_name = race_data.get('goal', {}).get('name', '')
    if goal_name in seasons.goals():
        return True
    if goal_name != POST_SEASON_GOAL_NAME:
        return False

    info_bot = race_data.get('info_bot', '') or ''
    return any(
        info_bot.startswith(f'{prefix} | Scheduled:')
        for prefix in (*seasons.room_prefixes(), POST_SEASON_ROOM_INFO_PREFIX)
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
    return any(info.startswith(AUTUMN_ROOM_INFO_PREFIX)
               or re.match(r'^\d{4} Autumn Tournament \u2014 ', info)
               for info in (info_user, info_bot))


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


def _ttp_tournament(edition):
    """The season's tournament record, for a numbered edition."""
    try:
        number = int(str(edition))
    except (TypeError, ValueError):
        return None
    season = next((s for s in seasons.oldest_first() if s.get('number') == number), None)
    return (season or {}).get('tournament') or None


def _ttp_name(edition):
    label = (_ttp_tournament(edition) or {}).get('roomLabel')
    if not label:
        # No guess. A room named for the wrong competition is recognised by the
        # wrong code; a room not opened is a log line somebody reads.
        raise ValueError('no TTP season {} tournament on file'.format(edition))
    return label


#: Every tournament the runner opens rooms for, by the bracket engine's event
#: name. An event not listed has no name, and never borrows Autumn's.
TOURNAMENT_NAMES = {
    'autumn': lambda edition: '{} Autumn Tournament'.format(edition or '2026'),
    'corto': lambda edition: 'Torneo Corto #{}'.format(edition),
    'ttp': _ttp_name,
}


def tournament_name(event, edition):
    try:
        name = TOURNAMENT_NAMES[event]
    except KeyError:
        raise ValueError('unknown tournament event {!r}'.format(event)) from None
    return name(edition)


def is_ttp_tournament_room(race_data):
    """Return True for a TTP season tournament room this bot opened.

    The title starts with the season's room label. Either field may carry it:
    info_user is ours, and SahasrahBot overwrites info_bot when it rolls.
    """
    goal_name = race_data.get('goal', {}).get('name', '')
    for season in seasons.oldest_first():
        tournament = season.get('tournament') or {}
        label, goal = tournament.get('roomLabel'), tournament.get('goal')
        if not label or goal_name != goal:
            continue
        if any((race_data.get(field) or '').startswith(label + ' — ')
               for field in ('info_user', 'info_bot')):
            return True
    return False


def is_corto_room(race_data):
    if race_data.get('goal', {}).get('name') != POST_SEASON_GOAL_NAME:
        return False
    return any(re.match(r'^Torneo Corto #\d+ \u2014 ', race_data.get(field) or '')
               for field in ('info_user', 'info_bot'))


# Presentation metadata must not change a room's recovery identity or racer names.
def room_identity(title):
    return re.sub(r'\nRestream: https://www\.twitch\.tv/[a-z0-9_]+$', '', title or '', flags=re.I)


def with_broadcast_channel(title, channel):
    channel = (channel or '').strip()
    if not re.fullmatch(r'[a-zA-Z0-9_]+', channel):
        return title
    return '{}\nRestream: https://www.twitch.tv/{}'.format(title, channel.lower())
