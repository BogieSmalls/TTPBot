import asyncio
from datetime import datetime, timedelta

import aiohttp

from racetime_bot import Bot

from .config import (
    RACE_NUMBER_MAP,
    ROOM_OPEN_MINUTES_BEFORE,
    TIMEZONE,
    WEBHOOK_MINUTES_BEFORE,
)
from .handler import TTPRaceHandler
from .paths import data_dir as configured_data_dir
from .schedule import get_upcoming_races, race_goal_for_time, race_info_for_time
from .room_policy import is_ttp_scheduled_room
from .state import DestinationStateStore, UNCERTAIN_RACE

from .provider import ProviderConfigurationError

def build_state_stores(provider, data_directory=None):
    if provider is None:
        raise ValueError('a validated Racetime provider is required for scheduler state')
    root = configured_data_dir() if data_directory is None else data_directory
    created = DestinationStateStore('created_races.json', provider.destination_key,
                                    'created_races', data_dir=root)
    sent = DestinationStateStore('sent_webhooks.json', provider.destination_key,
                                 'sent_webhooks', data_dir=root)
    return created, sent



def race_room_form_data(scheduled_time):
    """Return Racetime form fields for a scheduled TTP room."""
    return {
        'goal': race_goal_for_time(scheduled_time),
        'info_bot': race_info_for_time(scheduled_time),
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


class TTPBot(Bot):
    """
    Extends racetime_bot.Bot with a scheduling loop that creates
    Triforce Triple Play race rooms 30 minutes before their scheduled start.
    """

    scan_races_every = 10

    def __init__(self, *args, provider=None, discord_webhook_url=None,

                 race_seekers_role_id=None, data_dir=None,
                 created_race_store=None, sent_webhook_store=None, **kwargs):
        self.provider = provider
        self.discord_webhook_url = discord_webhook_url
        self.race_seekers_role_id = race_seekers_role_id
        if created_race_store is None or sent_webhook_store is None:
            default_created, default_sent = build_state_stores(provider, data_dir)
            created_race_store = created_race_store or default_created
            sent_webhook_store = sent_webhook_store or default_sent
        self.created_race_store = created_race_store
        self.sent_webhook_store = sent_webhook_store
        super().__init__(*args, **kwargs)
        self.created_races = self._load_created_races()
        self.sent_webhooks = self._load_sent_webhooks()

    def _load_created_races(self):
        return self.created_race_store.load()

    def _save_created_races(self):
        self.created_race_store.save(self.created_races)

    def _load_sent_webhooks(self):
        return set(self.sent_webhook_store.load())

    def _save_sent_webhooks(self):
        self.sent_webhook_store.save({key: True for key in self.sent_webhooks})

    def get_handler_class(self):
        return TTPRaceHandler

    def get_handler_kwargs(self, ws_conn, state):
        return {
            'conn': ws_conn,
            'logger': self.logger,
            'state': state,
            'command_prefix': '!',
        }

    def should_handle(self, race_data):
        """Handle only TTP-managed rooms.

        The z1r category on racetime.gg is shared with the wider Z1R community,
        so TTPBot stays out of races it did not schedule.
        """
        return super().should_handle(race_data) and is_ttp_scheduled_room(race_data)

    def run(self):
        """Add the race scheduler task alongside the standard bot tasks."""
        self.loop.create_task(self.reauthorize())
        self.loop.create_task(self.refresh_races())
        self.loop.create_task(self.race_scheduler())
        self.loop.set_exception_handler(self.handle_exception)
        self.loop.run_forever()

    async def race_scheduler(self):
        """Check every 60 seconds if a race room needs to be opened."""
        # Wait for the first refresh_races cycle to populate self.races,
        # so _ttp_room_already_exists can detect existing rooms.
        await asyncio.sleep(self.scan_races_every + 5)
        while True:
            try:
                await self._check_and_create_races()
            except Exception:
                self.logger.error('Error in race scheduler', exc_info=True)
            await asyncio.sleep(60)

    async def _check_and_create_races(self):
        now = datetime.now(TIMEZONE)

        # Clean up stale tracking entries (older than 2 hours)
        cutoff = now - timedelta(hours=2)
        stale = [k for k in self.created_races if datetime.fromisoformat(k) <= cutoff]
        if stale:
            for key in stale:
                del self.created_races[key]
            self._save_created_races()

        stale_wh = [k for k in self.sent_webhooks if datetime.fromisoformat(k) <= cutoff]
        if stale_wh:
            for key in stale_wh:
                self.sent_webhooks.discard(key)
            self._save_sent_webhooks()

        upcoming = get_upcoming_races(now, window_minutes=ROOM_OPEN_MINUTES_BEFORE + 5)

        for race_time in upcoming:
            race_key = race_time.isoformat()
            minutes_until = (race_time - now).total_seconds() / 60

            # Create room if not yet created
            if race_key not in self.created_races:
                if minutes_until <= ROOM_OPEN_MINUTES_BEFORE:
                    race_url = await self._create_race_room(race_time)
                    if race_url:
                        self.created_races[race_key] = race_url
                        self._save_created_races()

            # Send Race Seekers webhook once the window opens.
            # Checked every scheduler tick so it survives process restarts.
            race_url = self.created_races.get(race_key, '')
            if (race_url
                    and race_url != UNCERTAIN_RACE
                    and race_key not in self.sent_webhooks
                    and minutes_until <= WEBHOOK_MINUTES_BEFORE):
                self.loop.create_task(self._send_webhook(race_time, race_url))
                self.sent_webhooks.add(race_key)
                self._save_sent_webhooks()

    async def _create_race_room(self, scheduled_time):
        """Create a room through the configured Racetime provider."""
        formatted = scheduled_time.strftime('%a %b %d, %I:%M %p %Z')
        self.logger.info('Creating race room for %s', formatted)

        try:
            async with aiohttp.request(
                method='post',
                url=self.provider.http_url(
                    f'/o/{self.provider.category}/startrace'
                ),
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                data=race_room_form_data(scheduled_time),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 201:
                    room_url = self.provider.resolve_location(
                        resp.headers.get('Location')
                    )
                    self.logger.info('Race room created: %s', room_url)
                    return room_url
                self.logger.error(
                    'Race provider rejected room creation (HTTP %d)',
                    resp.status,
                )
        except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as exc:
            recovered = await self._recover_uncertain_room(scheduled_time)
            if recovered:
                self.logger.warning('Recovered room after uncertain provider response')
                return recovered
            self.logger.error(
                'Room creation outcome is uncertain; persisted fail-closed marker (%s)',
                type(exc).__name__,
            )
            return UNCERTAIN_RACE
        except (ProviderConfigurationError, aiohttp.ClientError,
                TypeError) as exc:
            self.logger.error(
                'Race room creation failed safely (%s)', type(exc).__name__
            )
        return None

    async def _recover_uncertain_room(self, scheduled_time):
        """Read current rooms once; never blindly retry an uncertain POST."""
        try:
            async with aiohttp.request(
                method='get',
                url=self.provider.http_url(
                    '/{}/data'.format(self.provider.category)
                ),
                headers={'Authorization': f'Bearer {self.access_token}'},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
            races = data.get('current_races', []) if isinstance(data, dict) else []
            expected_info = race_info_for_time(scheduled_time)
            matches = []
            for race in races:
                if not isinstance(race, dict) or race.get('info_bot') != expected_info:
                    continue
                raw_url = race.get('url')
                if not raw_url and isinstance(race.get('name'), str):
                    raw_url = '/' + race['name'].lstrip('/')
                matches.append(self.provider.resolve_location(raw_url))
            unique = sorted(set(matches))
            return unique[0] if len(unique) == 1 else None
        except (ProviderConfigurationError, aiohttp.ClientError,
                asyncio.TimeoutError, TypeError, ValueError):
            return None

    async def _send_webhook(self, scheduled_time, race_url):
        """Post Race Seekers announcement to Discord."""
        if not self.discord_webhook_url and not self.race_seekers_role_id:
            self.logger.warning('Discord announcements are disabled')
            return False
        if not self.discord_webhook_url or not self.race_seekers_role_id:
            self.logger.error('Discord announcement configuration is incomplete')
            return False
        try:
            race_url = self.provider.resolve_location(race_url)
        except ProviderConfigurationError:
            self.logger.error('Announcement room URL belongs to another destination')
            return False

        race_time = scheduled_time.time()
        number, use_prev_day = RACE_NUMBER_MAP.get(race_time, (None, False))
        if number is None:
            self.logger.warning('No race number mapping for %s', race_time)
            return False

        if use_prev_day:
            day_name = (scheduled_time - timedelta(days=1)).strftime('%A')
        else:
            day_name = scheduled_time.strftime('%A')

        message = (
            f'<@&{self.race_seekers_role_id}> '
            f'{day_name} TTP{number}: {race_url}'
        )
        self.logger.info('Sending webhook: %s', message)

        try:
            async with aiohttp.request(
                method='post',
                url=self.discord_webhook_url,
                json={
                    'content': message,
                    'allowed_mentions': {
                        'parse': [],
                        'roles': [self.race_seekers_role_id],
                    },
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (200, 204):
                    self.logger.info('Webhook sent successfully')
                    return True
                self.logger.error('Webhook failed (HTTP %d)', resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError) as exc:
            self.logger.error('Webhook failed safely (%s)', type(exc).__name__)
        return False
