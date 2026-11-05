"""The flag registry: friendly names for flag strings, without a deploy.

Council edits it, the site render publishes https://z1rracing.com/flagsets.json,
and this polls that file. A file is checked whole; a bad one changes nothing, and
the last good copy stays, here and on disk. The built-in lists in config.py apply
only when no copy has ever loaded, so a deleted shortcut stays deleted.

Imports only config, so flag_summary can use it with no import cycle.
"""
import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path

import aiohttp

from .config import PRESET_ALIASES, SEED_PRESETS

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 300
FETCH_TIMEOUT_SECONDS = 10

NAME_MAX = 80
DESCRIPTION_MAX = 500
FLAGS = re.compile(r'[A-Za-z0-9!]{1,200}')
MAX_SAFE_INTEGER = 2 ** 53 - 1

# Vanilla: the one real flag string under !flags' length guard, and not a preset.
SHORT_FLAG_STRINGS = frozenset({'8m6YW18uL'})


def reduce(text):
    """Everything that compares names and shortcuts compares this form."""
    return re.sub(r'[^a-z0-9]', '', str(text if text is not None else '').lower())


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_snapshot(snapshot):
    """Problems with a whole snapshot; empty when valid. Same contract as every reader."""
    if not isinstance(snapshot, dict):
        return ['snapshot must be an object']
    problems = []
    if not _is_int(snapshot.get('schemaVersion')) or snapshot.get('schemaVersion') != 1:
        problems.append('unsupported schema')
    revision = snapshot.get('revision')
    if not _is_int(revision) or revision < 1 or revision > MAX_SAFE_INTEGER:
        problems.append('invalid revision')
    flagsets = snapshot.get('flagsets')
    if not isinstance(flagsets, list) or not flagsets:
        return problems + ['at least one flagset required']

    string_owner = {}
    form_owner = {}
    seen_names = set()

    def claim(typed, name):
        form = reduce(typed)
        holder = form_owner.get(form)
        if holder is not None and holder != name:
            problems.append(f'"{typed}" clashes: it is on both "{holder}" and "{name}"')
        else:
            form_owner[form] = name

    for flagset in flagsets:
        if not isinstance(flagset, dict):
            problems.append('a flagset must be an object')
            continue
        name = flagset.get('name')
        if not isinstance(name, str) or not reduce(name) or len(name) > NAME_MAX:
            problems.append(f'invalid name: {name!r}')
            continue
        # Absent and null differ in JavaScript's check, so read it the same way.
        if 'description' not in flagset:
            problems.append(f'"{name}": invalid description')
        else:
            description = flagset['description']
            if description is not None and (not isinstance(description, str) or len(description) > DESCRIPTION_MAX):
                problems.append(f'"{name}": invalid description')
        if name in seen_names:
            problems.append(f'"{name}": name used twice')
        seen_names.add(name)
        claim(name, name)
        shortcuts = flagset.get('shortcuts')
        if not isinstance(shortcuts, list):
            problems.append(f'"{name}": shortcuts required')
        else:
            for shortcut in shortcuts:
                if not isinstance(shortcut, str) or not reduce(shortcut):
                    problems.append(f'"{name}": invalid shortcut {shortcut!r}')
                else:
                    claim(shortcut, name)
        strings = flagset.get('strings')
        if not isinstance(strings, list) or not strings:
            problems.append(f'"{name}": at least one flag string required')
            continue
        latest = 0
        for entry in strings:
            if not isinstance(entry, dict) or not isinstance(entry.get('flags'), str) or not FLAGS.fullmatch(entry['flags']):
                problems.append(f'"{name}": invalid flag string {entry!r}')
                continue
            if not isinstance(entry.get('isLatest'), bool):
                problems.append(f'"{name}": isLatest must be true or false')
            if entry.get('isLatest') is True:
                latest += 1
            owner = string_owner.get(entry['flags'])
            if owner is not None:
                problems.append(f'flag string {entry["flags"]} is on both "{owner}" and "{name}"')
            else:
                string_owner[entry['flags']] = name
        if latest != 1:
            problems.append(f'"{name}": exactly one flag string must be latest')
    return problems


class FlagsetRegistry:
    def __init__(self, url, cache_path, presets, aliases):
        self.url = url
        self.cache_path = Path(cache_path) if cache_path else None
        self._built_in = {reduce(key): flags for key, flags in presets.items()}
        for alias, key in aliases.items():
            if key in presets:
                self._built_in[reduce(alias)] = presets[key]
        self._built_in_strings = frozenset(presets.values()) | SHORT_FLAG_STRINGS
        self._forms = None
        self._shortcuts = None
        self._names = None

    @property
    def loaded(self):
        """Whether a good copy has ever loaded. From then on the built-in lists don't apply."""
        return self._forms is not None

    def resolve(self, word):
        """A name or shortcut's latest string, or None."""
        forms = self._built_in if self._forms is None else self._forms
        return forms.get(reduce(word))

    def is_shortcut(self, word):
        """Whether a bare !word rolls: listed shortcuts only. A name alone is for !race and
        !summary, so a plain word like !random can't use up a room's one roll."""
        return reduce(word) in (self._built_in if self._shortcuts is None else self._shortcuts)

    def is_registered(self, flags):
        if self._names is None:
            return flags in self._built_in_strings
        return flags in self._names

    def name_of(self, flags):
        """The name of the flagset holding this exact string, at any version."""
        return (self._names or {}).get(flags)

    def install(self, snapshot):
        """Take a copy already known to be valid."""
        forms = {}
        shortcuts = set()
        names = {}
        for flagset in snapshot['flagsets']:
            latest = next(entry['flags'] for entry in flagset['strings'] if entry['isLatest'])
            for typed in [flagset['name'], *flagset['shortcuts']]:
                forms[reduce(typed)] = latest
            shortcuts.update(reduce(shortcut) for shortcut in flagset['shortcuts'])
            for entry in flagset['strings']:
                names[entry['flags']] = flagset['name']
        self._forms = forms
        self._shortcuts = shortcuts
        self._names = names

    def load_cache(self):
        """The last good copy from disk, so a restart needs nothing from the network."""
        if not self.cache_path:
            return
        try:
            snapshot = json.loads(self.cache_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning('Flag registry cache unreadable: %s', exc)
            return
        problems = validate_snapshot(snapshot)
        if problems:
            logger.warning('Flag registry cache ignored: %s', problems[0])
            return
        self.install(snapshot)

    async def refresh(self, session):
        """Fetch, check whole, then take it. Anything short of that keeps what we have."""
        async with session.get(self.url, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)) as response:
            if response.status != 200:
                raise RuntimeError(f'HTTP {response.status}')
            text = await response.text()
        snapshot = json.loads(text)
        problems = validate_snapshot(snapshot)
        if problems:
            raise ValueError(f'invalid file: {problems[0]}')
        self.install(snapshot)
        self._write_cache(text)

    def _write_cache(self, text):
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.cache_path.parent, prefix=f'.{self.cache_path.name}.')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write(text)
            os.replace(temporary, self.cache_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    async def run(self):
        """Keep the copy fresh for the life of the process. Nothing here can stop the bot."""
        self.load_cache()
        if not self.url:
            logger.info('Flag registry off: no Z1RR_FLAGSETS_URL')
            return
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self.refresh(session)
                except Exception as exc:
                    logger.warning('Flag registry not refreshed; keeping the last good copy: %s', exc)
                await asyncio.sleep(REFRESH_SECONDS)


def _default_cache_path():
    configured = (os.environ.get('Z1RR_FLAGSETS_CACHE') or '').strip()
    if configured:
        return configured
    data_dir = (os.environ.get('TTPBOT_DATA_DIR') or '').strip()
    return Path(data_dir) / 'flagsets.json' if data_dir else None


registry = FlagsetRegistry(
    (os.environ.get('Z1RR_FLAGSETS_URL') or '').strip() or None,
    _default_cache_path(),
    SEED_PRESETS,
    PRESET_ALIASES,
)
