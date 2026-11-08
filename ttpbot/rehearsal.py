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


def own_room_names(created):
    """Race names ('z1r/slug') of the rooms this bot opened, from the TTP runner's created store."""
    names = set()
    for value in (created or {}).values():
        if isinstance(value, str) and value != UNCERTAIN_RACE and value.startswith('http'):
            path = urlsplit(value).path.strip('/')
            if path:
                names.add(path)
    return names
