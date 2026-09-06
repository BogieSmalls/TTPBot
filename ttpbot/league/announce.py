"""Announce League rooms to Discord.

Message content is assembled from a live spreadsheet, so mentions are
allow-listed rather than parsed: only the two racers' ids can ping.
"""

import asyncio

import aiohttp

#: Kept as constants so the escapes cannot be mangled by tooling again.
NEWLINE = chr(10)

#: Said when the control plane answered `continuation`: the channel is already
#: live with the previous race and the operator swaps the room and racers
#: over, so no booth was created for this one. Without it, crew open the booth
#: mid-show and reasonably think something is broken.
ALREADY_ON_AIR = (
    'The channel is already ON THE AIR with the previous race - watch Twitch '
    'and join the booth when it is time to set up this one.'
)
#: A blank line between the matchup and the crew credits - they are two
#: separate thoughts, and Discord renders a single break too tightly to scan.
BLANK_LINE = NEWLINE * 2


def _mention(racer):
    if racer.discord_id:
        return '<@{}>'.format(racer.discord_id)
    return racer.display_name


def _crew_line(label, names, crew, allowed):
    """One '<label>: @a @b' segment, or '' when nobody is scheduled."""
    if not names:
        return ''
    if crew is None:
        # No directory - the control plane may never have been reachable.
        # Credit people by name rather than dropping them from the post.
        rendered = [name for name in names if name]
        ids = []
    else:
        rendered, ids = crew.mentions(names)
    if not rendered:
        return ''
    allowed.extend(ids)
    return '{}: {}'.format(label, ' '.join(rendered))


def build_announcement(race, race_url, crew=None, continuation=False):
    """Return the webhook JSON body for a League room.

    `crew` resolves the sheet's Comms/Tracker names to Discord ids. It is
    optional on purpose: a race that cannot resolve its crew is announced with
    plain names, never skipped.
    """
    racers = (race.runner_one, race.runner_two)
    allowed = [r.discord_id for r in racers if r.discord_id]
    content = 'League: {} vs {} — {}'.format(
        _mention(race.runner_one), _mention(race.runner_two), race_url,
    )
    segments = [
        _crew_line('Comms', getattr(race, 'comms', ()), crew, allowed),
        _crew_line(
            'Tracker',
            (race.tracker,) if getattr(race, 'tracker', None) else (),
            crew, allowed,
        ),
    ]
    staffed = ' · '.join(segment for segment in segments if segment)
    if staffed:
        content = BLANK_LINE.join((content, staffed))
    if continuation:
        content = BLANK_LINE.join((content, ALREADY_ON_AIR))
    return {
        'content': content,
        'allowed_mentions': {
            'parse': [],
            # De-duplicated because someone can be both a racer elsewhere and
            # crew here, and Discord rejects a repeated id.
            'users': sorted(set(allowed)),
        },
    }


async def send_league_announcement(
    race, race_url, webhook_url, logger, crew=None, continuation=False,
):
    """Post the League announcement. Returns True when Discord accepted it."""
    if not webhook_url:
        logger.warning('League Discord announcements are not configured')
        return False
    body = build_announcement(race, race_url, crew=crew, continuation=continuation)
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
