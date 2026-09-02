"""The League scheduling loop.

Runs alongside TTPBot's own scheduler as a separate task with separate
state, so a League failure cannot affect TTP scheduling.
"""

import asyncio
from datetime import timedelta
from urllib.parse import urlsplit

import aiohttp

from ..config import DEFAULT_SCHEDULE_URL
from ..state import UNCERTAIN_RACE
from .announce import send_league_announcement
from .rooms import create_league_room
from .schedule import parse_schedule

LEAGUE_ROOM_OPEN_MINUTES_BEFORE = 30
LEAGUE_START_BUFFER_MINUTES = 5
SCHEDULE_CACHE_MAX_AGE = timedelta(hours=6)
STATE_RETENTION = timedelta(hours=2)
TICK_SECONDS = 60


class ScheduleSource:
    """Fetch and cache the League schedule CSV."""

    def __init__(self, url, roster, logger):
        self.url = url
        self.roster = roster
        self.logger = logger
        self._races = []
        self._fetched_at = None

    async def races(self, now):
        try:
            async with aiohttp.request(
                method='get', url=self.url,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status != 200:
                    raise aiohttp.ClientError(
                        'HTTP {}'.format(response.status))
                body = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            return self._stale(now, exc)

        parsed = parse_schedule(body, self.roster, self.logger)
        if not parsed and self._races:
            # A 200 with zero usable races, after previously having some,
            # means the sheet likely stopped being world-readable (Google
            # serves an HTML sign-in page with HTTP 200) or its column
            # shape changed. Keep serving the cached races and leave
            # _fetched_at untouched so the staleness guard still applies,
            # rather than silently opening zero rooms forever.
            self.logger.error(
                'League schedule fetch returned no usable races; '
                'keeping previous snapshot of %d race(s)', len(self._races))
            return list(self._races)

        self._races = parsed
        self._fetched_at = now
        return list(self._races)

    def _stale(self, now, exc):
        """Reuse the last good snapshot, but not indefinitely."""
        if self._fetched_at is None:
            self.logger.error(
                'League schedule unavailable and no cached copy (%s)',
                type(exc).__name__)
            return []
        age = now - self._fetched_at
        if age > SCHEDULE_CACHE_MAX_AGE:
            self.logger.error(
                'League schedule stale by %s; opening no rooms', age)
            return []
        self.logger.warning(
            'League schedule fetch failed (%s); using cached copy %s old',
            type(exc).__name__, age)
        return list(self._races)


def _race_name(room_url):
    """Return the racetime race name ('z1r/slug') for a room URL."""
    return urlsplit(room_url).path.strip('/')


class LeagueScheduler:
    def __init__(self, bot, source, created_store, webhook_store,
                 webhook_url, logger):
        self.bot = bot
        self.source = source
        self.created_store = created_store
        self.webhook_store = webhook_store
        self.webhook_url = webhook_url
        self.logger = logger
        self.created = created_store.load()
        self.announced = set(webhook_store.load())

    async def run(self):
        """Tick forever. Never let a League failure kill the process."""
        while True:
            try:
                await self.tick(self._now())
            except Exception:
                self.logger.error('Error in League scheduler', exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    @staticmethod
    def _now():
        from datetime import datetime

        from ..config import TIMEZONE
        return datetime.now(TIMEZONE)

    def _prune(self, now):
        cutoff = now - STATE_RETENTION
        retained = self.created_store.cleanup_before(cutoff)
        if retained != self.created:
            self.created = retained
        webhooks = self.webhook_store.cleanup_before(cutoff)
        if set(webhooks) != self.announced:
            self.announced = set(webhooks)

    async def tick(self, now):
        self._prune(now)
        for race in await self.source.races(now):
            try:
                await self._handle(race, now)
            except Exception:
                self.logger.error('Error handling League race %s',
                                  race.title, exc_info=True)

    async def _handle(self, race, now):
        minutes_until = (race.start - now).total_seconds() / 60
        if minutes_until > LEAGUE_ROOM_OPEN_MINUTES_BEFORE:
            return
        if minutes_until < -LEAGUE_START_BUFFER_MINUTES:
            return

        room_url = self.created.get(race.key)
        if room_url is None:
            room_url = await create_league_room(
                race, self.bot.provider, self.bot.access_token, self.logger)
            if not room_url:
                return
            self.created[race.key] = room_url
            self.created_store.save(self.created)
            if room_url != UNCERTAIN_RACE:
                self._seed_handler_state(race, room_url)

        if room_url == UNCERTAIN_RACE or race.key in self.announced:
            return
        await send_league_announcement(race, room_url, self.webhook_url, self.logger)
        self.announced.add(race.key)
        self.webhook_store.save({key: True for key in self.announced})

    def _seed_handler_state(self, race, room_url):
        """Tell the handler who to invite before it exists.

        Bot.create_handler only creates state[race_name] when absent, and
        passes it into the handler by reference, so seeding it here reaches
        the handler untouched.
        """
        race_name = _race_name(room_url)
        if not race_name:
            return
        entry = self.bot.state.setdefault(race_name, {})
        entry['league_race'] = {
            'invite': [race.runner_one.racetime_id, race.runner_two.racetime_id],
            'title': race.title,
        }
