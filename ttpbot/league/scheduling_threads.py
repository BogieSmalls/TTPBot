"""Weekly scheduling threads in Discord's #league-scheduling.

The rules require teams to arrange their two races in a thread, and until now
those threads were opened by hand: seven a week, named seven different ways,
sometimes missing the week. This opens them on a fixed schedule, one per
fixture, with both rosters pinged and the away/home order stated, so nobody has
to remember who declares first.

Thread creation needs a bot token; a webhook cannot create threads. Posting is
best effort per fixture: one failure must never cost the other six threads.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re

import aiohttp

from ..config import TIMEZONE

NON_ALNUM = re.compile(r'[^a-z0-9]+')

#: Season 1 week start dates, from the League rules doc. The Matchups tab
#: carries week numbers but no dates, and the rules table is the only place the
#: two are tied together.
WEEK_STARTS = {
    1: date(2026, 9, 1),
    2: date(2026, 9, 8),
    3: date(2026, 9, 15),
    4: date(2026, 9, 22),
    5: date(2026, 9, 29),
    6: date(2026, 10, 6),
    7: date(2026, 10, 13),
}

#: Midnight ET at the start of the day before the week begins, so a team
#: opening Discord that day finds the thread already waiting instead of it
#: arriving the evening before racing starts. A whole day to agree a time.
THREAD_OPEN_TIME = time(0, 0)

#: How long after the opening time a missed week is still opened. A bot that
#: was down at midnight should still open the threads later that day; it
#: should not open week 2's threads in week 5.
OPEN_WINDOW = timedelta(hours=36)

#: Where the agreed date and time is recorded. The short link is the one the
#: League Team posts in #league-announcements, and it survives the form being
#: rebuilt behind it.
SCHEDULING_FORM_URL = 'https://forms.gle/dFpq13NUxPLPqUJ78'

#: Discord's limit is 100 characters. Team names are long ("Dodongo Ate My
#: Baby" vs "Horny Steel Crushers" is already 60 with the week prefix).
THREAD_NAME_LIMIT = 100

PUBLIC_THREAD = 11
#: One week: the thread stays reachable for the whole scheduling window.
AUTO_ARCHIVE_MINUTES = 10080


def open_at(week, starts=None):
    """When week `week`'s threads should be opened, or None if unknown."""
    start = (starts or WEEK_STARTS).get(week)
    if start is None:
        return None
    return datetime.combine(start - timedelta(days=1), THREAD_OPEN_TIME, tzinfo=TIMEZONE)


def week_due(now, starts=None):
    """The week whose threads are due now, or None.

    Only one week is ever due: the windows are a week apart and OPEN_WINDOW is
    much shorter than that.
    """
    for week in sorted((starts or WEEK_STARTS)):
        opens = open_at(week, starts)
        if opens is None:
            continue
        if opens <= now < opens + OPEN_WINDOW:
            return week
    return None


def thread_key(week, fixture, starts=None):
    """State key: the week's opening timestamp, then the fixture.

    Shaped for DestinationStateStore, which requires `<iso timestamp>|<slug>`
    and prunes by that timestamp.
    """
    opens = open_at(week, starts)
    slug = 'w{}-{}-{}'.format(
        week,
        NON_ALNUM.sub('', fixture.away.lower()) or 'unknown',
        NON_ALNUM.sub('', fixture.home.lower()) or 'unknown',
    )
    return '{}|{}'.format(opens.isoformat(), slug)


def thread_name(week, fixture):
    """`Week 4 - Away @ Home`, trimmed to Discord's limit."""
    name = 'Week {} - {} @ {}'.format(week, fixture.away, fixture.home)
    return name[:THREAD_NAME_LIMIT]


@dataclass(frozen=True)
class ThreadPost:
    content: str
    allowed_user_ids: list


def _team_line(label, team, roster):
    """`**Team** (away for game 1): @a @b @c`, names only where no Discord id."""
    mentions, ids = [], []
    for racer in roster.by_team(team):
        if racer.discord_id:
            mentions.append('<@{}>'.format(racer.discord_id))
            ids.append(racer.discord_id)
        else:
            mentions.append(racer.display_name)
    if not mentions:
        # An unknown team name is worth saying out loud: it means the Matchups
        # tab and the roster disagree, and somebody has to fix one of them.
        return '**{}** ({}): roster not found'.format(team, label), []
    return '**{}** ({}): {}'.format(team, label, ' '.join(mentions)), ids


def build_thread_post(week, fixture, roster, week_label=None):
    """The opening message: who is who, who declares first, and the format."""
    away_line, away_ids = _team_line('away for game 1', fixture.away, roster)
    home_line, home_ids = _team_line('home for game 1', fixture.home, roster)
    heading = 'Week {}'.format(week)
    if week_label:
        heading = '{} - {}'.format(heading, week_label)
    lines = [
        '**{}**'.format(heading),
        '',
        away_line,
        home_line,
        '',
        'Game 1: **{}** declare their racer(s) first, then **{}**.'.format(fixture.away, fixture.home),
        'Game 2: the sides swap - **{}** are home, **{}** are away.'.format(fixture.away, fixture.home),
        'Once game 1 is set, sort out game 2. The games may be played in either order.',
        '',
        'Each of the week\'s two races must be raced by a different team member '
        '(co-op weeks excepted). If a team changes racer(s) after scheduling, '
        'the opponent may change theirs too. If you cannot reach your opponent, '
        'tell the League Team.',
        '',
        'Agree a date and time here, then submit it on the scheduling form: '
        '<{}>'.format(SCHEDULING_FORM_URL),
    ]
    return ThreadPost(content='\n'.join(lines), allowed_user_ids=away_ids + home_ids)


class DiscordThreads:
    """The two Discord calls this needs, and nothing else."""

    API = 'https://discord.com/api/v10'

    def __init__(self, token, channel_id, logger):
        self.token = token
        self.channel_id = channel_id
        self.logger = logger

    @property
    def configured(self):
        return bool(self.token and self.channel_id)

    async def _call(self, method, path, payload):
        headers = {'Authorization': 'Bot {}'.format(self.token), 'User-Agent': 'TTPBot'}
        async with aiohttp.request(
            method=method, url=self.API + path, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            body = await response.json(content_type=None)
            if response.status >= 300:
                raise aiohttp.ClientError('HTTP {}: {}'.format(response.status, body))
            return body

    async def create_thread(self, name):
        thread = await self._call('post', '/channels/{}/threads'.format(self.channel_id), {
            'name': name,
            'type': PUBLIC_THREAD,
            'auto_archive_duration': AUTO_ARCHIVE_MINUTES,
        })
        return thread['id']

    async def post(self, thread_id, post):
        await self._call('post', '/channels/{}/messages'.format(thread_id), {
            'content': post.content,
            # Only the twelve rostered players can be pinged from this post,
            # whatever a team name happens to contain.
            'allowed_mentions': {'parse': [], 'users': post.allowed_user_ids},
        })


async def open_week_threads(week, fixtures, roster, threads, opened, logger,
                            week_label=None, starts=None):
    """Create any missing threads for `week`. Returns the keys opened.

    `opened` holds the keys already done, so a restart mid-week reopens
    nothing. A fixture that fails is simply retried on the next tick.
    """
    created = []
    for fixture in fixtures:
        key = thread_key(week, fixture, starts)
        if key in opened:
            continue
        try:
            thread_id = await threads.create_thread(thread_name(week, fixture))
            await threads.post(thread_id, build_thread_post(week, fixture, roster, week_label))
        except Exception:
            logger.error('League scheduling thread failed for %s vs %s',
                         fixture.away, fixture.home, exc_info=True)
            continue
        logger.info('Opened League scheduling thread %s for week %d', thread_id, week)
        created.append(key)
    return created
