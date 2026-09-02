"""Create League race rooms.

Deliberately separate from TTPBot._create_race_room. The duplication is
worth it: TTP Season 5 is live, and League must not be able to change how
TTP rooms are opened.
"""

import asyncio

import aiohttp

from ..config import POST_SEASON_GOAL_NAME
from ..provider import ProviderConfigurationError
from ..state import UNCERTAIN_RACE


def league_room_form_data(race):
    """Return Racetime form fields for a League room."""
    return {
        'goal': POST_SEASON_GOAL_NAME,
        'info_bot': race.title,
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


async def create_league_room(race, provider, access_token, logger):
    """Open a League room. Returns a URL, UNCERTAIN_RACE, or None."""
    logger.info('Creating League room: %s', race.title)
    try:
        async with aiohttp.request(
            method='post',
            url=provider.http_url('/o/{}/startrace'.format(provider.category)),
            headers={
                'Authorization': 'Bearer {}'.format(access_token),
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data=league_room_form_data(race),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status == 201:
                room_url = provider.resolve_location(response.headers.get('Location'))
                logger.info('League room created: %s', room_url)
                return room_url
            logger.error('Provider rejected League room creation (HTTP %d)',
                         response.status)
    except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as exc:
        recovered = await _recover_uncertain_league_room(race, provider,
                                                        access_token, logger)
        if recovered:
            logger.warning('Recovered League room after uncertain response')
            return recovered
        logger.error(
            'League room creation is uncertain; persisted fail-closed marker (%s)',
            type(exc).__name__,
        )
        return UNCERTAIN_RACE
    except (ProviderConfigurationError, aiohttp.ClientError, TypeError) as exc:
        logger.error('League room creation failed safely (%s)', type(exc).__name__)
    return None


async def _recover_uncertain_league_room(race, provider, access_token, logger):
    """Read current rooms once; never blindly retry an uncertain POST."""
    try:
        async with aiohttp.request(
            method='get',
            url=provider.http_url('/{}/data'.format(provider.category)),
            headers={'Authorization': 'Bearer {}'.format(access_token)},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
        races = data.get('current_races', []) if isinstance(data, dict) else []
        matches = []
        for candidate in races:
            if not isinstance(candidate, dict):
                continue
            if candidate.get('info_bot') != race.title:
                continue
            raw_url = candidate.get('url')
            if not raw_url and isinstance(candidate.get('name'), str):
                raw_url = '/' + candidate['name'].lstrip('/')
            matches.append(provider.resolve_location(raw_url))
        unique = sorted(set(matches))
        return unique[0] if len(unique) == 1 else None
    except (ProviderConfigurationError, aiohttp.ClientError,
            asyncio.TimeoutError, TypeError, ValueError):
        return None
