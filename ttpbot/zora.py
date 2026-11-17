"""Zora mode: TTPBot serving ZORA's own RaceTime.gg category.

ZORA is a Z1R variant, and each of its races has two flag strings: the Z1R
flags and the ZORA flags. A process whose category is `zora` joins every open
room there (the category is ZORA's alone, unlike z1r) and runs nothing of the
z1r category's: no nightly rooms, League, tournaments or grace minutes.

Network-free, so the handler, the bot and the tests can all use it.
"""

import os
import re
from urllib.parse import urlencode

from .config import ZORA_CATEGORY_SLUG

# Same range as z1r rolls. It stays below zora.gg's seed limit of 2**64, so every
# rolled seed fits in a zora.gg link.
SEED_MAX = 8999999999999999999

# The ZORA web generator. A link to it carries both flag strings and the seed.
ZORA_URL = 'https://zora.gg/'

# `<version>.<payload>`, as zora.gg's flags spec writes it (zora.pattern). The
# payload's digits are the Z1R alphabet; `_` is read as `!`.
ZORA_FLAGS = re.compile(r'[1-9][0-9]*\.[0-9A-Za-z!_]{1,200}')

# What a zora room says where z1r would say GENERIC_WELCOME_MESSAGE.
ZORA_WELCOME_MESSAGE = (
    "Hi, I'm TTPBot. I can help with ZORA seed rolling and hash confirmation. "
    "Type !help to see available commands."
)
ZORA_FLAGS_USAGE = 'Usage: !flags <flagstring> [zora flags]'

# The commands a zora room answers. Every other command is silent there.
ZORA_COMMANDS = frozenset({'flags', 'race', 'cancel', 'summary', 'help'})

# A zora process refuses to start with any of these on: each is a z1r feature,
# and switching one on for zora is a misconfiguration, never half a feature.
Z1R_FEATURES = (
    'TTPBOT_LEAGUE_ENABLED',
    'TTPBOT_LEAGUE_RESULTS_ENABLED',
    'TTPBOT_GRACE_ENABLED',
    'TTPBOT_NIGHTLY_BOOTHS_ENABLED',
    'TTPBOT_REHEARSAL',
    'Z1RR_AUTUMN_ENABLED',
    'Z1RR_CORTO_ENABLED',
    'Z1RR_TTP_ENABLED',
)
Z1R_DATA = '/var/lib/ttpbot'


def is_zora_category(slug):
    return slug == ZORA_CATEGORY_SLUG


def zora_flags(text):
    """A ZORA flag string as ZORA writes it (`_` read as `!`), or None if it isn't one."""
    if not isinstance(text, str) or not ZORA_FLAGS.fullmatch(text):
        return None
    return text.replace('_', '!')


def seed_link(seed, flags, zora):
    """A zora.gg link that opens the generator with these flags and this seed.

    zora.gg's own order: flags, then zora (left out when empty), then seed. The
    seed goes last because chat apps often drop a link's trailing `!`.
    """
    params = [('flags', flags)]
    if zora:
        params.append(('zora', zora))
    params.append(('seed', str(seed)))
    return f'{ZORA_URL}?{urlencode(params)}'


def race_info(seed, flags, zora):
    """Race info for a zora roll, in the order z1r's !flags writes.

    `ZORA:` rather than `ZORA Flags:`, so the `Flags: (\\S+)` readers find the
    Z1R string and nothing else.
    """
    return f'Seed: {seed} - Flags: {flags} - ZORA: {zora or "none"} - {seed_link(seed, flags, zora)}'


def _on(value):
    return (value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def startup_problems(environ, config):
    """Why a zora process must not start; empty when it may, and always for z1r."""
    if not is_zora_category(config.category_slug):
        return []
    env = os.environ if environ is None else environ
    problems = ['{} is on; a zora process runs no z1r features'.format(key)
                for key in Z1R_FEATURES if _on(env.get(key))]
    if getattr(config, 'discord_webhook_url', None):
        problems.append('TTPBOT_DISCORD_WEBHOOK_URL is set; a zora process posts no announcements')
    data = (config.data_dir or '').rstrip('/')
    if data == Z1R_DATA or data.startswith(Z1R_DATA + '/'):
        problems.append("TTPBOT_DATA_DIR must be the zora service's own path")
    return problems
