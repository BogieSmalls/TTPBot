"""Create Autumn Tournament race rooms.

Separate from `league/rooms.py` and from `TTPBot._create_race_room` for the same
reason those are separate from each other: TTP Season 5 and the League are live,
and the tournament must not be able to change how their rooms are opened.

The contract this owes its caller is narrow and load-bearing:

    a URL          the room exists, and this is it
    UNCERTAIN_RACE a room may exist; nobody opens another one
    None           nothing was created, and trying again is safe

`None` is a *claim*, and only a definite rejection earns it. A timeout, a
connection that dropped, a 5xx or a response that could not be read all come
after the request was already on its way -- so the room may exist, and calling
that "not created" is how a match gets two rooms.

That last case is where this deliberately differs from the League's version,
which returns `None` for any non-201 including a 500. A 500 arrives after
racetime has the POST; it is not evidence that nothing happened.
"""

import asyncio

import aiohttp

from ..config import POST_SEASON_GOAL_NAME
from ..provider import ProviderConfigurationError
from ..state import UNCERTAIN_RACE
from ..room_policy import tournament_name, room_identity, with_broadcast_channel

#: The match id goes in the room's own info, because that is what recovery reads.
#: A pair of names is not enough -- the grand final and its reset are the same two
#: people -- so the thing written here has to be the identity, not the matchup.
TITLE_MARKER = '[{}]'

ROOM_TIMEOUT_SECONDS = 15
RECOVER_TIMEOUT_SECONDS = 10


def room_title(race, label=None):
    """What the room calls itself, and what recovery looks for.

    Readable first, identifiable second: a racer sees who is playing, and the
    bracketed match id at the end is what tells `_recover` that an existing room
    is *this* match's rather than the same pair's other one.
    """
    matchup = '{} vs {}'.format(race.runner_one, race.runner_two)
    parts = [tournament_name(race.identity.event, race.identity.edition)]
    if label:
        parts.append(label)
    parts.append(matchup)
    title = '{} {}'.format(' — '.join(parts), TITLE_MARKER.format(race.match_id))
    if getattr(race, 'best_of', 1) > 1 or race.identity.game > 1:
        title += ' Game {}'.format(race.identity.game)
    return title


def autumn_room_form_data(race, label=None):
    """Racetime form fields for an Autumn room.

    Ranked, unlike a League co-op room: a tournament match between two people is
    exactly what a rating is for.
    """
    return {
        'goal': POST_SEASON_GOAL_NAME,
        # info_user only, as the League learned: racetime renders both fields, so
        # writing the title to each showed it twice, and info_bot is what
        # `setinfo` overwrites -- SahasrahBot replaces it when it rolls a seed.
        # Recovery depends on this field, so it must be one nobody else owns.
        'info_user': with_broadcast_channel(room_title(race, label), getattr(race, 'channel', None)),
        'invitational': 'false',
        'unlisted': 'false',
        'start_delay': '15',
        'time_limit': '4',
        'streaming_required': 'true',
        'auto_start': 'true',
        'allow_prerace_chat': 'true',
        'allow_midrace_chat': 'true',
        'allow_non_entrant_chat': 'true',
        'chat_message_delay': '0',
        'hide_comments': 'true',
    }


async def create_autumn_room(race, provider, access_token, logger, label=None,
                             requester=None):
    """Open the room. Returns a URL, UNCERTAIN_RACE, or None."""
    if not access_token:
        logger.error('Autumn: no racetime access token, so no room was opened')
        return None

    title = room_title(race, label)
    request = requester if requester is not None else aiohttp.request
    logger.info('Autumn: creating room for %s -- %s', race.match_id, title)

    try:
        async with request(
            method='post',
            url=provider.http_url('/o/{}/startrace'.format(provider.category)),
            headers={
                'Authorization': 'Bearer {}'.format(access_token),
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data=autumn_room_form_data(race, label),
            timeout=aiohttp.ClientTimeout(total=ROOM_TIMEOUT_SECONDS),
        ) as response:
            if response.status == 201:
                try:
                    room_url = provider.resolve_location(response.headers.get('Location'))
                except (ProviderConfigurationError, TypeError):
                    return await _uncertain(
                        race, provider, access_token, logger, title, requester,
                        'created response has no valid room location')
                logger.info('Autumn room created for %s: %s', race.match_id, room_url)
                return room_url

            if response.status >= 500:
                # The POST reached racetime. A 500 is racetime failing to tell us
                # what happened to it, not evidence that nothing did.
                return await _uncertain(
                    race, provider, access_token, logger, title, requester,
                    'HTTP {}'.format(response.status))

            logger.error(
                'Autumn: racetime rejected the room for %s (HTTP %d); nothing was '
                'created', race.match_id, response.status)
            return None

    except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as exc:
        return await _uncertain(
            race, provider, access_token, logger, title, requester,
            type(exc).__name__)
    except (ProviderConfigurationError, TypeError) as exc:
        # The request was never well-formed enough to send, so nothing was
        # created and saying so is safe.
        logger.error(
            'Autumn: the room request for %s could not be built (%s)',
            race.match_id, type(exc).__name__)
        return None
    except aiohttp.ClientError as exc:
        # Anything else aiohttp raises may or may not have been sent. Not a
        # claim of absence.
        return await _uncertain(
            race, provider, access_token, logger, title, requester,
            type(exc).__name__)


async def _uncertain(race, provider, access_token, logger, title, requester, why):
    """Look for the room once, then give up and say we do not know.

    Read, never re-POST. A retry is the one thing that turns "maybe a room" into
    "definitely two rooms".
    """
    found = await _recover(provider, access_token, logger, title, requester)
    if found:
        logger.warning(
            'Autumn: the room for %s came back unreadable (%s) but was found at %s',
            race.match_id, why, found)
        return found
    logger.error(
        'Autumn: the room for %s is uncertain (%s) and was not found. It is '
        'recorded as uncertain; no second room will be opened.',
        race.match_id, why)
    return UNCERTAIN_RACE


async def _recover(provider, access_token, logger, title, requester=None):
    """The open room whose info is this match's, if there is exactly one."""
    titles = {title} if isinstance(title, str) else set(title)
    request = requester if requester is not None else aiohttp.request
    try:
        async with request(
            method='get',
            url=provider.http_url('/{}/data'.format(provider.category)),
            headers={'Authorization': 'Bearer {}'.format(access_token)},
            timeout=aiohttp.ClientTimeout(total=RECOVER_TIMEOUT_SECONDS),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)

        races = data.get('current_races', []) if isinstance(data, dict) else []
        found = []
        for candidate in races:
            if not isinstance(candidate, dict):
                continue
            if not titles.intersection(room_identity(candidate.get(field)) for field in ('info_user', 'info_bot')):
                continue
            raw = candidate.get('url')
            if not raw and isinstance(candidate.get('name'), str):
                raw = '/' + candidate['name'].lstrip('/')
            found.append(provider.resolve_location(raw))

        unique = sorted(set(found))
        if len(unique) > 1:
            # Two rooms already claiming this match. Picking one would hide the
            # problem, and the problem needs a person.
            logger.error(
                'Autumn: more than one open room says it is %r: %s',
                title, ', '.join(unique))
            return None
        return unique[0] if unique else None
    except (ProviderConfigurationError, aiohttp.ClientError,
            asyncio.TimeoutError, TypeError, ValueError):
        return None


async def recover_autumn_room(race, provider, access_token, logger, label=None):
    if not getattr(race, 'room_marker', None):
        return None
    # Keep recovering rooms opened before the display-name change. Two matches
    # across either naming format still refuse rather than selecting a room.
    if race.identity.event != 'autumn':
        return await _recover(provider, access_token, logger, {room_title(race, label)})
    parts = ['Z1R Autumn'] + ([label] if label else [])
    parts.append('{} vs {}'.format(race.runner_one, race.runner_two))
    legacy = '{} [{}] Game {} [{}]'.format(' \u2014 '.join(parts), race.match_id,
                                          race.identity.game, race.room_marker)
    return await _recover(provider, access_token, logger, {room_title(race, label), legacy})
