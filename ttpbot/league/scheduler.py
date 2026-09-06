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
from .booth import BoothOutcome, request_booth
from .broadcast_request import build_broadcast_request
from .wake import wake_control_plane
from .rooms import create_league_room
from .matchups import parse_matchups
from .schedule import parse_schedule

LEAGUE_ROOM_OPEN_MINUTES_BEFORE = 30
LEAGUE_START_BUFFER_MINUTES = 5
SCHEDULE_CACHE_MAX_AGE = timedelta(hours=6)
STATE_RETENTION = timedelta(hours=2)
TICK_SECONDS = 60

#: Crew changes rarely and the control plane sleeps, so this is deliberately
#: slack: often enough that a roster edit lands well before the next race,
#: rare enough that a sleeping CP is not polled every minute.
CREW_REFRESH_SECONDS = 1800

#: Wake the control plane five minutes before the room opens, so it is ready
#: by the time the booth is asked for. /api/wake waits for readiness rather
#: than for the instance to report RUNNING, which is what makes the margin
#: safe rather than hopeful.
LEAGUE_WAKE_MINUTES_BEFORE = 35


class ScheduleSource:
    """Fetch and cache the League schedule CSV."""

    def __init__(self, url, roster, logger, matchups_url=None):
        self.url = url
        self.roster = roster
        self.logger = logger
        self.matchups_url = matchups_url
        self._races = []
        self._fetched_at = None
        self._matchups = None

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

        matchups = await self._matchup_table()
        parsed = parse_schedule(body, self.roster, self.logger, matchups=matchups)
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

    async def _matchup_table(self):
        """The Matchups tab, fetched once and then kept for the process.

        A season's fixtures are fixed, so this does not need re-reading every
        minute. Failure is not fatal and deliberately so: parse_schedule just
        leaves `fixture` unset, which costs the booth but still opens the room
        and posts the announcement.
        """
        if self._matchups is not None or not self.matchups_url:
            return self._matchups
        try:
            async with aiohttp.request(
                method='get', url=self.matchups_url,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status != 200:
                    raise aiohttp.ClientError(
                        'HTTP {}'.format(response.status))
                body = await response.text()
        except Exception as exc:
            # Deliberately broad, unlike the schedule fetch above. A schedule
            # that will not load has no races to lose; this one is optional
            # enrichment, and anything escaping here would leave tick() to
            # catch it and skip room creation for every race this minute.
            self.logger.warning(
                'League matchups fetch failed (%s); no booths until it succeeds',
                type(exc).__name__)
            return None

        parsed = parse_matchups(body, self.logger)
        if not parsed.count:
            # Google answers 200 with an HTML sign-in page once a sheet stops
            # being world-readable. Caching that would silently disable every
            # booth for the life of the process.
            self.logger.error(
                'League matchups parsed to zero fixtures; will retry')
            return None
        self.logger.info(
            'League matchups loaded: %d fixture(s)', parsed.count)
        self._matchups = parsed
        return parsed

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
                 webhook_url, logger, crew=None, roster_url=None,
                 roster_token=None, relay_wake_url=None, relay_wake_token=None,
                 wake_target='production', booth_url=None, booth_token=None):
        self.bot = bot
        self.source = source
        self.created_store = created_store
        self.webhook_store = webhook_store
        self.webhook_url = webhook_url
        self.logger = logger
        self.created = created_store.load()
        self.announced = set(webhook_store.load())
        self.crew = crew
        self.roster_url = roster_url
        self.roster_token = roster_token
        self._crew_refreshed_at = None
        self.relay_wake_url = relay_wake_url
        self.relay_wake_token = relay_wake_token
        self.wake_target = wake_target
        self.booth_url = booth_url
        self.booth_token = booth_token
        #: Race keys whose control plane has been woken and crew re-read.
        self._prepared = set()
        #: Race keys whose booth request came back with a definite answer.
        #: Kept apart from `announced` so an unanswered booth is retried while
        #: the Discord post still goes out exactly once.
        self._booth_done = set()

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

    async def _refresh_crew(self, now):
        """Top up the crew roster, at most once every CREW_REFRESH_SECONDS.

        Best effort by design: a failure leaves the cached roster in place and
        the announcement falls back to plain names.
        """
        if self.crew is None or not self.roster_url or not self.roster_token:
            return
        if self._crew_refreshed_at is not None:
            elapsed = (now - self._crew_refreshed_at).total_seconds()
            if elapsed < CREW_REFRESH_SECONDS:
                return
        self._crew_refreshed_at = now
        await self.crew.refresh(self.roster_url, self.roster_token)

    async def tick(self, now):
        await self._refresh_crew(now)
        self._prune(now)
        for race in await self.source.races(now):
            try:
                await self._handle(race, now)
            except Exception:
                self.logger.error('Error handling League race %s',
                                  race.title, exc_info=True)

    async def _prepare_control_plane(self, race):
        """Wake the control plane, then force a fresh crew lookup.

        Retried on every tick until it works: /api/wake is idempotent, so a
        repeat against an already-woken plane costs nothing, and a relay that
        was briefly down still gets covered before the room opens.

        The crew lookup here deliberately ignores the ordinary interval. A
        lookup that failed at T-40 against a sleeping control plane would not
        try again for thirty minutes, which lands after the announcement -
        so without this the T-30 post can tag nobody even though the control
        plane came up at T-35.
        """
        if race.key in self._prepared:
            return
        if not race.channel or not self.relay_wake_url or not self.relay_wake_token:
            return
        try:
            await wake_control_plane(
                self.relay_wake_url, self.logger,
                token=self.relay_wake_token, target=self.wake_target,
            )
        except Exception:
            # Never out of the tick: Phase 1 does not depend on any of this.
            self.logger.warning(
                'League wake failed for %s; will retry before the room opens', race.title,
                exc_info=True,
            )
            return
        if self.crew is None or not self.roster_url or not self.roster_token:
            self._prepared.add(race.key)
            return
        try:
            refreshed = await self.crew.refresh(self.roster_url, self.roster_token)
        except Exception:
            self.logger.warning('League crew lookup failed after wake', exc_info=True)
            return
        if not refreshed:
            # CrewDirectory reports failure by returning False rather than by
            # raising. Marking the race prepared here would retire the retry
            # and leave the T-30 post tagging nobody.
            self.logger.warning(
                'League crew lookup after wake reported failure for %s; will retry',
                race.title,
            )
            return
        self._crew_refreshed_at = None
        self._prepared.add(race.key)

    async def _request_booth(self, race, room_url):
        """Ask the control plane for a booth. Never raises.

        The answer decides what the announcement says, which is why this runs
        before it rather than after.
        """
        if race.key in self._booth_done:
            return BoothOutcome()
        if not race.channel or not self.booth_url or not self.booth_token:
            return BoothOutcome()
        payload = build_broadcast_request(race, _room_slug(room_url), self.crew, self.logger)
        if payload is None:
            return BoothOutcome()
        try:
            outcome = await request_booth(
                payload, self.booth_url, self.booth_token, self.logger)
        except Exception:
            # A booth failure must never cost the room its announcement.
            self.logger.warning('League booth request failed for %s', race.title, exc_info=True)
            return BoothOutcome()
        if outcome.outcome is not None:
            self._booth_done.add(race.key)
        return outcome

    async def _handle(self, race, now):
        minutes_until = (race.start - now).total_seconds() / 60
        if minutes_until < -LEAGUE_START_BUFFER_MINUTES:
            # Races that are over come first. The sheet keeps every past week,
            # so without this a restart wakes a control plane once per
            # historical row that happened to have a Channel.
            return
        if minutes_until <= LEAGUE_WAKE_MINUTES_BEFORE:
            await self._prepare_control_plane(race)
        if minutes_until > LEAGUE_ROOM_OPEN_MINUTES_BEFORE:
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

        if room_url == UNCERTAIN_RACE:
            return

        # Booth first, announcement second: a continuation has to carry the
        # already-on-air warning and only the control plane knows that. The
        # booth call sits ahead of the `announced` guard so an attempt whose
        # answer never arrived is retried on later ticks; the announcement
        # itself is still posted exactly once.
        booth = await self._request_booth(race, room_url)

        if race.key in self.announced:
            return
        await send_league_announcement(
            race, room_url, self.webhook_url, self.logger, crew=self.crew,
            continuation=booth.is_continuation,
        )
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


def _room_slug(room_url):
    """`z1r/clever-slug-1234` from the room URL the racetime API returned."""
    return (room_url or '').split('://', 1)[-1].split('/', 1)[-1].strip('/')
