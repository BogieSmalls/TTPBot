"""TTP Nightly booths: Council staffs a nightly race, TTPBot opens its booth.

Each scheduled nightly race (8 PM, 10 PM...) is registered in the central
competitions owner as a TTP Nightly occurrence, keyed by its start, so Council
can staff it on the Council page before its room or runners exist. Registering
again is harmless: the owner returns the occurrence as it stands and never
touches its crew, offers, channel or qualifier tag.

When the race's reminder goes out (20 minutes before), a staffed occurrence -
one with a channel booked - gets its booth: TTPBot asks the control plane for
the nightly booth, with Council's crew and the room it opened. Unstaffed races
are unchanged: they run and are announced as always, with no booth.

None of this may cost a race its room or its announcement, so every call here
logs and carries on.
"""
import asyncio
from datetime import timedelta, timezone
from urllib.parse import urlparse

import aiohttp

from .league.booth import request_booth
from .league.wake import wake_control_plane
from .schedule import get_races_for_date

#: How far ahead nightly races are registered for Council to staff.
REGISTER_DAYS = 7
#: The 7-day window moves a day at a time, so once a day picks up each new race (Bogie, 2026-10-25).
#: Registering again is harmless; a restart registers on its first tick.
REGISTER_EVERY = timedelta(days=1)
#: A staffed nightly's booth opens with the race's reminder.
BOOTH_MINUTES_BEFORE = 20
#: ...and can still be opened after the race starts, if Council books a channel late:
#: a race can go on stream last minute, like any other (Bogie, 2026-10-25). Three hours
#: is how long the Council page keeps a started nightly listed.
BOOTH_HOURS_AFTER_START = 3
OWNER_TIMEOUT_SECONDS = 8


def booth_window_open(race_time, now):
    """From the reminder until three hours after the start: when a staffed nightly may get its booth."""
    return (race_time - timedelta(minutes=BOOTH_MINUTES_BEFORE) <= now
            < race_time + timedelta(hours=BOOTH_HOURS_AFTER_START))


def races_in_booth_window(now):
    """Today's and yesterday's nightly races whose booth window is open (a 10 PM race runs past midnight)."""
    races = []
    for day in (now - timedelta(days=1), now):
        races.extend(race for race in get_races_for_date(day.date()) if booth_window_open(race, now))
    return races


def occurrence_match_id(race_time):
    """The owner's identity for a nightly: its UTC start, compact ('N20261106T010000Z')."""
    return 'N' + race_time.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def occurrence_key(race_time):
    """The control plane's booth key for a nightly: ttp:<UTC start, ISO>."""
    return 'ttp:' + race_time.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _room_slug(room_url):
    """'z1r/brave-goriya-1234' from a racetime room URL; None before the room exists."""
    if not room_url or not room_url.startswith('http'):
        return None
    return urlparse(room_url).path.strip('/') or None


def booth_request(race_time, occurrence, room_url, crew, logger):
    """The nightly booth request, or None when Council has not booked a channel for it."""
    bookings = occurrence.get('bookings') or []
    channel = bookings[0].get('channel') if bookings else None
    if not channel:
        return None
    people = occurrence.get('crew') or {}

    def user_id(slot):
        name = (people.get(slot) or {}).get('name')
        if not name:
            return None
        who = crew.user_id_for(name) if crew else None
        if not who:
            logger.warning('TTP Nightly crew %r has no managed Restream user id', name)
        return who

    qualifier = 'corto-qualifier' in (occurrence.get('associations') or [])
    title = 'TTP Nightly' + (' · Torneo Corto qualifier' if qualifier else '')
    payload = {
        'requestKey': occurrence_key(race_time),
        'twitchChannel': channel,
        'scheduledAt': race_time.astimezone(timezone.utc).isoformat(),
        'title': title,
        # Runners are chosen in the booth once the room fills.
        'racers': [],
        'commentatorUserIds': [who for who in (user_id('comms1'), user_id('comms2')) if who],
        'trackerUserId': user_id('tracker'),
        'tracker2UserId': user_id('tracker2'),
    }
    if not (payload['commentatorUserIds'] or payload['trackerUserId'] or payload['tracker2UserId']):
        return None
    slug = _room_slug(room_url)
    if slug:
        payload['raceSlug'] = slug
    return payload


class NightlyBooths:
    """Registers nightly occurrences and opens the booths Council staffed."""

    def __init__(self, *, owner_url, owner_environment, edition, actor, reader_token, writer_token,
                 booth_url, booth_token, crew, logger, relay_wake_url='', relay_wake_token='',
                 wake_target='production', requester=None):
        self.owner_url = (owner_url or '').rstrip('/')
        self.environment = owner_environment
        self.edition_id = 'ttp-nightly:' + edition
        self.actor = actor
        self.reader_token = reader_token
        self.writer_token = writer_token
        self.booth_url = booth_url
        self.booth_token = booth_token
        self.crew = crew
        self.logger = logger
        self.relay_wake_url = relay_wake_url
        self.relay_wake_token = relay_wake_token
        self.wake_target = wake_target
        self.requester = requester
        self._registered_at = None
        # Last booth request per occurrence: a repeat is sent only when it changes (the room arrived).
        self._sent = {}

    @property
    def configured(self):
        return bool(self.owner_url and self.environment and self.actor and self.reader_token)

    def _request(self):
        return self.requester if self.requester is not None else aiohttp.request

    async def _owner(self, method, route, token, body=None):
        async with self._request()(
            method=method,
            url=self.owner_url + '/v1/competitions' + route,
            json=body,
            headers={'Authorization': 'Bearer {}'.format(token), 'x-z1rr-environment': self.environment},
            timeout=aiohttp.ClientTimeout(total=OWNER_TIMEOUT_SECONDS),
        ) as response:
            if response.status == 404:
                return None
            if response.status != 200:
                raise RuntimeError('competitions owner HTTP {}'.format(response.status))
            return await response.json()

    async def register_upcoming(self, now):
        """Registers the coming week's nightly races (daily at most). Never raises."""
        if not self.configured or not self.writer_token:
            return 0
        if self._registered_at and now - self._registered_at < REGISTER_EVERY:
            return 0
        count = 0
        try:
            for day in range(REGISTER_DAYS):
                for race_time in get_races_for_date((now + timedelta(days=day)).date()):
                    if race_time <= now:
                        continue
                    start = race_time.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    await self._owner('POST', '/nightlies', self.writer_token, {
                        'editionId': self.edition_id, 'startsAt': start,
                        # One request id per occurrence: a repeat is the same request, answered by its receipt.
                        'requestId': 'nightly-register:' + start, 'actor': self.actor,
                    })
                    count += 1
            self._registered_at = now
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError, ValueError) as exc:
            self.logger.warning('TTP Nightly registration failed (%s); will retry', type(exc).__name__)
        return count

    async def maybe_open_booth(self, race_time, room_url, now):
        """At the reminder, a staffed nightly's booth; the room attaches when it exists. Never raises."""
        if not self.configured or not self.booth_url or not self.booth_token:
            return None
        if not booth_window_open(race_time, now):
            return None
        game_id = '{}:{}:1'.format(self.edition_id, occurrence_match_id(race_time))
        try:
            occurrence = await self._owner('GET', '/games/' + game_id, self.reader_token)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError, ValueError) as exc:
            self.logger.warning('TTP Nightly staffing read failed (%s)', type(exc).__name__)
            return None
        if not occurrence:
            return None
        payload = booth_request(race_time, occurrence, room_url, self.crew, self.logger)
        if payload is None:
            return None
        key = payload['requestKey']
        if self._sent.get(key) == payload:
            return None
        if self.relay_wake_url:
            try:
                await wake_control_plane(self.relay_wake_url, self.logger, token=self.relay_wake_token,
                                         target=self.wake_target)
            except Exception:
                self.logger.warning('TTP Nightly: control plane wake failed; will retry', exc_info=True)
                return None
        outcome = await request_booth(payload, self.booth_url, self.booth_token, self.logger, self.requester,
                                      endpoint='/internal/relay/ttp/broadcast', label='TTP Nightly')
        if outcome.outcome is not None:
            self._sent[key] = payload
        return outcome
