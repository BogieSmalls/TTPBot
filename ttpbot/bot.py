import asyncio
import json
from datetime import datetime, timedelta

import aiohttp

from racetime_bot import Bot

from .config import (
    GOAL_NAME,
    POST_SEASON_GOAL_NAME,
    RACE_NUMBER_MAP,
    ROOM_OPEN_MINUTES_BEFORE,
    TIMEZONE,
    TTP_ROOM_INFO_PREFIXES,
    WEBHOOK_MINUTES_BEFORE,
    Z1R_DISCORD_WEBHOOK_URL,
)
from .handler import TTPRaceHandler
from .paths import ensure_parent_dir, runtime_path
from .schedule import get_upcoming_races, race_goal_for_time, race_info_for_time

CREATED_RACES_FILE = runtime_path('created_races.json')
SENT_WEBHOOKS_FILE = runtime_path('sent_webhooks.json')


def is_ttp_scheduled_room(race_data):
    """Return True for TTP-managed rooms, including labeled post-season rooms."""
    goal_name = race_data.get('goal', {}).get('name', '')
    if goal_name == GOAL_NAME:
        return True
    if goal_name != POST_SEASON_GOAL_NAME:
        return False

    info_bot = race_data.get('info_bot', '') or ''
    return any(
        info_bot.startswith(f'{prefix} | Scheduled:')
        for prefix in TTP_ROOM_INFO_PREFIXES
    )


def race_room_form_data(scheduled_time):
    """Return racetime.gg form fields for a scheduled TTP room."""
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

    def __init__(self, *args, provider=None, discord_webhook_url=None,
                 race_seekers_role_id=None, **kwargs):
        self.provider = provider
        self.discord_webhook_url = discord_webhook_url
        self.race_seekers_role_id = race_seekers_role_id
        super().__init__(*args, **kwargs)
        self.created_races = self._load_created_races()
        self.sent_webhooks = self._load_sent_webhooks()

    def _load_created_races(self):
        """Load persisted created_races dict {race_key: race_url} from disk."""
        try:
            with open(CREATED_RACES_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Migrate old format (list of keys) to dict
                    return {key: '' for key in data}
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_created_races(self):
        """Persist created_races dict to disk."""
        try:
            ensure_parent_dir(CREATED_RACES_FILE)
            with open(CREATED_RACES_FILE, 'w') as f:
                json.dump(self.created_races, f)
        except Exception:
            self.logger.error('Error saving created_races', exc_info=True)

    def _load_sent_webhooks(self):
        """Load persisted sent_webhooks set from disk."""
        try:
            with open(SENT_WEBHOOKS_FILE, 'r') as f:
                data = json.load(f)
                return set(data)
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_sent_webhooks(self):
        """Persist sent_webhooks set to disk."""
        try:
            ensure_parent_dir(SENT_WEBHOOKS_FILE)
            with open(SENT_WEBHOOKS_FILE, 'w') as f:
                json.dump(list(self.sent_webhooks), f)
        except Exception:
            self.logger.error('Error saving sent_webhooks', exc_info=True)

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
        """Only handle TTP-managed scheduled rooms."""
        if not is_ttp_scheduled_room(race_data):
            return False
        return super().should_handle(race_data)

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
                    self.created_races[race_key] = race_url or ''
                    self._save_created_races()

            # Send Race Seekers webhook once the window opens.
            # Checked every scheduler tick so it survives process restarts.
            race_url = self.created_races.get(race_key, '')
            if (race_url
                    and race_key not in self.sent_webhooks
                    and minutes_until <= WEBHOOK_MINUTES_BEFORE):
                self.loop.create_task(self._send_webhook(race_time, race_url))
                self.sent_webhooks.add(race_key)
                self._save_sent_webhooks()

    async def _create_race_room(self, scheduled_time):
        """Create a new TTP race room via the racetime.gg API."""
        formatted = scheduled_time.strftime('%a %b %d, %I:%M %p %Z')
        self.logger.info('Creating race room for %s', formatted)

        try:
            async with aiohttp.request(
                method='post',
                url=self.http_uri(f'/o/{self.category_slug}/startrace'),
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                data=race_room_form_data(scheduled_time),
            ) as resp:
                if resp.status == 201:
                    location = resp.headers.get('Location', '')
                    self.logger.info('Race room created: %s', location)
                    return f'https://racetime.gg{location}'
                else:
                    body = await resp.text()
                    self.logger.error(
                        'Failed to create race room (HTTP %d): %s',
                        resp.status,
                        body,
                    )
        except Exception:
            self.logger.error('Error creating race room', exc_info=True)
        return None

    async def _send_webhook(self, scheduled_time, race_url):
        """Post Race Seekers announcement to Discord."""
        if not Z1R_DISCORD_WEBHOOK_URL:
            self.logger.warning('TTPBOT_Z1R_WEBHOOK_URL not set; skipping announcement')
            return

        race_time = scheduled_time.time()
        number, use_prev_day = RACE_NUMBER_MAP.get(race_time, (None, False))
        if number is None:
            self.logger.warning('No race number mapping for %s', race_time)
            return

        if use_prev_day:
            day_name = (scheduled_time - timedelta(days=1)).strftime('%A')
        else:
            day_name = scheduled_time.strftime('%A')

        message = f'<@&1494076623442542735> {day_name} TTP{number}: {race_url}'
        self.logger.info('Sending webhook: %s', message)

        try:
            async with aiohttp.request(
                method='post',
                url=Z1R_DISCORD_WEBHOOK_URL,
                json={
                    'content': message,
                    'allowed_mentions': {'roles': ['1494076623442542735']},
                },
            ) as resp:
                if resp.status in (200, 204):
                    self.logger.info('Webhook sent successfully')
                else:
                    body = await resp.text()
                    self.logger.error(
                        'Webhook failed (HTTP %d): %s', resp.status, body,
                    )
        except Exception:
            self.logger.error('Error sending webhook', exc_info=True)
