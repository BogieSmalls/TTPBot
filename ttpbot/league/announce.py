"""Announce League rooms to Discord.

Message content is assembled from a live spreadsheet, so mentions are
allow-listed rather than parsed: only the two racers' ids can ping.
"""

import asyncio

import aiohttp


def _mention(racer):
    if racer.discord_id:
        return '<@{}>'.format(racer.discord_id)
    return racer.display_name


def build_announcement(race, race_url):
    """Return the webhook JSON body for a League room."""
    racers = (race.runner_one, race.runner_two)
    content = 'League: {} vs {} — {}'.format(
        _mention(race.runner_one), _mention(race.runner_two), race_url,
    )
    return {
        'content': content,
        'allowed_mentions': {
            'parse': [],
            'users': [r.discord_id for r in racers if r.discord_id],
        },
    }


async def send_league_announcement(race, race_url, webhook_url, logger):
    """Post the League announcement. Returns True when Discord accepted it."""
    if not webhook_url:
        logger.warning('League Discord announcements are not configured')
        return False
    body = build_announcement(race, race_url)
    logger.info('Announcing League room: %s', race.title)
    try:
        async with aiohttp.request(
            method='post',
            url=webhook_url,
            json=body,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status in (200, 204):
                return True
            logger.error('League webhook failed (HTTP %d)', response.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, TypeError) as exc:
        logger.error('League webhook failed safely (%s)', type(exc).__name__)
    return False
