"""Telling people about an Autumn room.

The Discord ids come from the engine, resolved through `seatFor` on its side, so
a name off the sheet reaches the right person whichever way the form spelled it.

Inviting racers is *not* here, and that is deliberate. The first version of this
module POSTed to `/o/<category>/<room>/invite`, which was invented: the League
has been inviting racers in production for months and does it over the racetime
websocket, through `handler.invite_user`, driven by `bot.state[race]`. Nothing in
this codebase has ever used an HTTP invite endpoint. Shipping an unverified call
that silently does nothing is worse than shipping no call at all, so the
scheduler keeps its `invite` seam and nothing is wired into it yet -- see
`wiring.py` for what connecting it would take.
"""

import asyncio

import aiohttp

#: Discord renders two newlines as a paragraph break; one is a soft wrap.
BLANK_LINE = '\n\n'

WEBHOOK_TIMEOUT_SECONDS = 10


def _mention(name, discord_id):
    """A ping when we know who somebody is, their name when we do not.

    Never skipped for want of an id. A racer named in plain text still reads
    correctly; a racer left out of their own match announcement does not.
    """
    return '<@{}>'.format(discord_id) if discord_id else name


def build_announcement(race, race_url, ids=None, label=None, crew=()):
    """The webhook body for an Autumn room.

    `ids` maps a racer's canonical name to a Discord id. Partial is fine.
    """
    ids = ids or {}
    one = _mention(race.runner_one, ids.get(race.runner_one))
    two = _mention(race.runner_two, ids.get(race.runner_two))

    headline = 'Z1R Autumn'
    if label:
        headline = '{} — {}'.format(headline, label)
    content = '{}: {} vs {} — {}'.format(headline, one, two, race_url)

    if crew:
        content = BLANK_LINE.join((content, 'Restream crew: {}'.format(
            ' · '.join(crew))))

    pinged = sorted({
        str(ids[name]) for name in (race.runner_one, race.runner_two)
        if ids.get(name)
    })
    return {
        'content': content,
        'allowed_mentions': {
            # Nothing is pinged except the two racers, by id. `parse: []` stops
            # an @everyone in a racer's name from becoming one.
            'parse': [],
            'users': pinged,
        },
    }


async def send_autumn_announcement(race, race_url, webhook_url, logger, ids=None,
                                   label=None, crew=(), requester=None,
                                   bot_token=None, channel_id=None):
    """Post it. Returns True only when Discord accepted it.

    The return value is what the scheduler's guard is set from, so a False here
    means it is tried again next tick rather than silently dropped.
    """
    headers = {}
    target = webhook_url
    if not target and bot_token and channel_id and str(channel_id).isdigit():
        target = 'https://discord.com/api/v10/channels/{}/messages'.format(channel_id)
        headers['Authorization'] = 'Bot ' + bot_token
    if not target:
        logger.warning('Autumn: Discord announcements are not configured')
        return False

    request = requester if requester is not None else aiohttp.request
    body = build_announcement(race, race_url, ids=ids, label=label, crew=crew)
    try:
        async with request(
            method='post', url=target, headers=headers, json=body,
            timeout=aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_SECONDS),
        ) as response:
            if response.status in (200, 204):
                logger.info('Autumn: announced %s', race.match_id)
                return True
            logger.error(
                'Autumn: Discord refused %s (HTTP %d)',
                race.match_id, response.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, TypeError) as exc:
        logger.error(
            'Autumn: Discord announcement failed for %s (%s)',
            race.match_id, type(exc).__name__)
    return False
