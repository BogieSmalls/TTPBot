"""TTPBOT_REHEARSAL: a temporary second TTPBot for a staging tournament rehearsal.

It runs beside production with the same racetime credentials, against a
staging engine. So it opens no nightly rooms, runs no League, Autumn or Corto
work, and handles only rooms its own engine had it open, which are unlisted
and unranked. Production never sets this.
"""

import os
from urllib.parse import urlsplit

from .state import UNCERTAIN_RACE


def enabled(environ=None):
    value = (os.environ if environ is None else environ).get('TTPBOT_REHEARSAL', '')
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


PRODUCTION_ENGINE_PORT = '3007'
PRODUCTION_DATA = '/var/lib/ttpbot'


def _snowflakes(value):
    return {part.strip() for part in (value or '').split(',') if part.strip().isdigit()}


def outbound_problems(environ):
    """What would let this rehearsal reach production; empty when it may start.

    It posts to Discord with the bot token itself (announcements, Live Ops), so
    every target must be one of TTPBOT_REHEARSAL_CHANNEL_IDS, and no webhook is
    allowed at all. A booth may only be woken or requested on Restream staging.
    """
    env = environ
    problems = []
    allowed = _snowflakes(env.get('TTPBOT_REHEARSAL_CHANNEL_IDS'))
    if not allowed:
        problems.append('TTPBOT_REHEARSAL_CHANNEL_IDS names no channel')
    for key in sorted(k for k in env if k.startswith('TTPBOT_') and k.endswith('_DISCORD_CHANNEL_ID')) + ['TTPBOT_LIVEOPS_THREAD_ID']:
        value = (env.get(key) or '').strip()
        if value and value not in allowed:
            problems.append('{} is not a rehearsal channel'.format(key))
    for key in sorted(k for k in env if k.startswith('TTPBOT_') and 'WEBHOOK' in k):
        if (env.get(key) or '').strip():
            problems.append('{} is set; a rehearsal posts by webhook nowhere'.format(key))
    booths = any((env.get(key) or '').strip() for key in ('Z1RR_RELAY_URL', 'Z1RR_CONTROL_PLANE_URL'))
    if booths and (env.get('Z1RR_WAKE_TARGET') or '').strip() != 'staging':
        problems.append('booths are configured without Z1RR_WAKE_TARGET=staging')
    engine = (env.get('Z1RR_ENGINE_URL') or '').strip()
    if not engine or (urlsplit(engine).port is not None and str(urlsplit(engine).port) == PRODUCTION_ENGINE_PORT):
        problems.append('Z1RR_ENGINE_URL must name the rehearsal engine')
    for key in ('TTPBOT_DATA_DIR', 'Z1RR_FLAGSETS_CACHE'):
        value = (env.get(key) or '').strip().rstrip('/')
        if not value or value == PRODUCTION_DATA or value.startswith(PRODUCTION_DATA + '/'):
            problems.append('{} must be the rehearsal\'s own path'.format(key))
    return problems


def startup_problems(environ, config):
    """Production: none. A rehearsal: its outbound problems, and any webhook given as an argument."""
    if not enabled(environ):
        return []
    problems = outbound_problems(environ)
    for name in ('discord_webhook_url', 'league_discord_webhook_url'):
        if getattr(config, name, None):
            problems.append('--{} is set; a rehearsal posts by webhook nowhere'.format(name.replace('_', '-')))
    return problems


def own_room_names(created):
    """Race names ('z1r/slug') of the rooms this bot opened, from the TTP runner's created store."""
    names = set()
    for value in (created or {}).values():
        if isinstance(value, str) and value != UNCERTAIN_RACE and value.startswith('http'):
            path = urlsplit(value).path.strip('/')
            if path:
                names.add(path)
    return names
