"""Fetching the Schedule tab, and knowing when not to believe it.

Split from the parser because the two fail differently. Parsing is total -- it
always returns what it could read -- while fetching has a failure the runner must
handle rather than absorb: a response that arrives, says 200, and is not the
sheet.

Google serves an HTML sign-in page with HTTP 200 once a tab stops being
world-readable. Parsed, that is zero rows, which is indistinguishable from a
council member having cleared the schedule. The two want opposite behavior:

    a genuinely empty tab   ->  every race is done or cancelled, open nothing
    a sign-in page          ->  we know nothing, keep what we had

So an unreadable response keeps the last good copy rather than reporting an empty
schedule, and says so once rather than every minute.
"""

import asyncio
from datetime import timedelta

import aiohttp

from .schedule import Schedule, parse_schedule, schedule_is_readable

#: How long a cached schedule is still worth using when fetches keep failing.
#: The League's figure, for the same reason: a race night is hours, and a copy
#: from this morning is a better guide to tonight than nothing at all.
CACHE_MAX_AGE = timedelta(hours=6)

FETCH_TIMEOUT_SECONDS = 20


class AutumnSource:
    """The Schedule tab, fetched and cached.

    `rows(now)` is the only thing a runner calls. It returns a `Schedule`, always
    -- never None and never a raised exception -- because a tick that dies on a
    fetch is a race night with no rooms.
    """

    def __init__(self, url, logger, fetch=None):
        self.url = url
        self.logger = logger
        #: Injected in tests. Production goes through aiohttp.
        self._fetch = fetch
        self._schedule = None
        self._fetched_at = None
        #: So an unreadable tab is reported once, not sixty times an hour.
        self._complained = False

    @property
    def configured(self):
        return bool(self.url)

    async def _body(self):
        if self._fetch:
            return await self._fetch(self.url)
        async with aiohttp.request(
            method='get', url=self.url,
            timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
        ) as response:
            if response.status != 200:
                raise aiohttp.ClientError('HTTP {}'.format(response.status))
            return await response.text()

    async def rows(self, now):
        """The schedule as of now, or the last good copy, or nothing."""
        if not self.configured:
            return Schedule(readable=False)

        try:
            body = await self._body()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            return self._stale(now, 'could not be fetched: {}'.format(exc))

        if not schedule_is_readable(body):
            # The response is not the tab. Distinct from an empty tab, and the
            # difference is whether tonight's rooms get opened.
            return self._stale(now, 'was not the schedule tab (a sign-in page?)')

        parsed = parse_schedule(body, self.logger)
        parsed.observed_at = now
        self._schedule = parsed
        self._fetched_at = now
        if self._complained:
            self.logger.info('Autumn schedule is readable again')
            self._complained = False
        return parsed

    def _stale(self, now, why):
        """The last good copy while it is recent enough, and nothing after that.

        Nothing, rather than the copy, once it is old: a schedule from two days
        ago will happily open a room for a race that was moved, and nobody is
        watching the log at that point.
        """
        if not self._complained:
            self.logger.error('Autumn schedule %s', why)
            self._complained = True

        if self._schedule is None or self._fetched_at is None:
            return Schedule(readable=False)
        if now - self._fetched_at > CACHE_MAX_AGE:
            self.logger.error(
                'Autumn schedule has been unreadable since %s, which is too long '
                'to keep acting on; opening nothing until it reads again',
                self._fetched_at.isoformat())
            return Schedule(readable=False)

        self.logger.warning(
            'Autumn: using the schedule as last read at %s',
            self._fetched_at.isoformat())
        return self._schedule
