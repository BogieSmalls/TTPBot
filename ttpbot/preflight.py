"""Read-only configuration, provider, state, and scheduler preflight."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path

import requests

from .bot import build_state_stores, is_ttp_scheduled_room
from .state import StateStoreError


class PreflightError(ValueError):
    """A provider probe failed without exposing its response or credentials."""


class HttpPreflightClient:
    def __init__(self, config, *, timeout=10):
        self.config = config
        self.timeout = timeout

    def acquire_token(self):
        try:
            response = requests.post(
                self.config.provider.http_url('/o/token'),
                data={
                    'grant_type': 'client_credentials',
                    'client_id': self.config.client_id,
                    'client_secret': self.config.client_secret,
                },
                timeout=self.timeout,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise PreflightError('OAuth token probe failed')
            value = response.json()
            token = value.get('access_token') if isinstance(value, dict) else None
            if not isinstance(token, str) or not token:
                raise PreflightError('OAuth token response was invalid')
            return token
        except (requests.RequestException, ValueError) as exc:
            raise PreflightError('OAuth token probe failed') from exc

    def category_data(self, token):
        try:
            response = requests.get(
                self.config.provider.http_url(
                    '/{}/data'.format(self.config.provider.category)
                ),
                headers={'Authorization': 'Bearer {}'.format(token)},
                timeout=self.timeout,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise PreflightError('category probe failed')
            value = response.json()
            if not isinstance(value, dict):
                raise PreflightError('category response was invalid')
            server_time = parsedate_to_datetime(response.headers.get('Date', ''))
            if server_time.tzinfo is None:
                server_time = server_time.replace(tzinfo=timezone.utc)
            return value, server_time.astimezone(timezone.utc)
        except (requests.RequestException, TypeError, ValueError) as exc:
            raise PreflightError('category probe failed') from exc


def _default_lock_probe(path='/run/ttpbot/scheduler.lock'):
    """Return True only when an existing Linux lock is held by another process."""
    target = Path(path)
    if os.name == 'nt' or not target.exists() or target.is_symlink():
        return False
    try:
        import fcntl
        with target.open('rb') as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            finally:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except (ImportError, OSError):
        return True
    return False


def _initial_checks(config):
    return {
        'configuration': bool(
            config.provider and config.client_id and config.client_secret
            and config.data_dir and config.environment
        ),
        'state': False,
        'data_directory': False,
        'announcements': bool(
            (config.discord_webhook_url and config.race_seekers_role_id)
            or (not config.discord_webhook_url and not config.race_seekers_role_id)
        ),
        'scheduler_lock_available': None,
        'oauth': None,
        'category': None,
        'clock_within_30_seconds': None,
        'clock_skew_seconds': None,
        'no_room_collision': None,
    }


def perform_preflight(
    config, *, probe=False, client=None, now=None, lock_probe=None
):
    """Return a secret-free preflight result; no room/webhook mutation is possible."""
    checks = _initial_checks(config)
    try:
        created, sent = build_state_stores(config.provider, config.data_dir)
        created.load()
        sent.load()
        checks['state'] = True
    except (StateStoreError, ValueError):
        checks['state'] = False
    data_directory = Path(config.data_dir)
    checks['data_directory'] = (
        data_directory.is_dir()
        and not data_directory.is_symlink()
        and os.access(str(data_directory), os.R_OK | os.W_OK)
    )

    if probe:
        held = (lock_probe or _default_lock_probe)()
        checks['scheduler_lock_available'] = not held
        provider_client = client or HttpPreflightClient(config)
        try:
            token = provider_client.acquire_token()
            checks['oauth'] = True
            category, server_time = provider_client.category_data(token)
            checks['category'] = category.get('slug') == config.provider.category
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None or server_time.tzinfo is None:
                raise PreflightError('probe clock was timezone-naive')
            skew = abs((server_time.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds())
            checks['clock_skew_seconds'] = round(skew, 3)
            checks['clock_within_30_seconds'] = skew <= 30
            races = category.get('current_races', [])
            if not isinstance(races, list):
                raise PreflightError('category current races were invalid')
            checks['no_room_collision'] = not any(
                isinstance(race, dict) and is_ttp_scheduled_room(race)
                for race in races
            )
        except Exception:
            # Result intentionally records only booleans; response bodies,
            # URLs, tokens, exception strings, and filesystem paths are omitted.
            if checks['oauth'] is None:
                checks['oauth'] = False
            if checks['category'] is None:
                checks['category'] = False
            if checks['clock_within_30_seconds'] is None:
                checks['clock_within_30_seconds'] = False
            if checks['no_room_collision'] is None:
                checks['no_room_collision'] = False

    required = [
        'configuration', 'state', 'data_directory', 'announcements',
    ]
    if probe:
        required.extend([
            'scheduler_lock_available', 'oauth', 'category',
            'clock_within_30_seconds', 'no_room_collision',
        ])
    return {
        'schema_version': 1,
        'mode': 'probe' if probe else 'check-config',
        'destination_key': config.provider.destination_key if config.provider else None,
        'checks': checks,
        'ok': all(checks[name] is True for name in required),
    }


def run_preflight(config):
    result = perform_preflight(config, probe=True)
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result['ok'] else 1
