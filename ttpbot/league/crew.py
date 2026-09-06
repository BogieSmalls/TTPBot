"""Crew identity for League races.

Racers come from a committed file because room creation must not depend on a
live API. Crew is different: the Comms and Tracker columns are a dropdown of
Z1RR.Restream user names, and that list changes whenever someone joins, leaves
or is deactivated. Reading it live is the point - a committed copy would go
stale silently and start tagging people who are no longer crew.

The control plane sleeps on idle, so it is regularly unreachable at announce
time. Every lookup therefore serves from an on-disk cache that outlives a
sleeping server, and a name that cannot be resolved degrades to plain text
rather than costing anyone their announcement.
"""

import asyncio
import json

import aiohttp

from ..paths import ensure_parent_dir

#: The roster is a convenience, never a gate. Kept short so a sleeping control
#: plane cannot hold up a race-room announcement.
FETCH_TIMEOUT_SECONDS = 8


def _clean(value):
    return value.strip() if isinstance(value, str) else ''


def _key(name):
    """Lookup key: the dropdown supplies exact names, but people retype them."""
    return _clean(name).lower()


class CrewDirectory:
    """Name -> Discord id for everyone eligible to be named as crew."""

    def __init__(self, cache_path, logger):
        self._cache_path = cache_path
        self._logger = logger
        self._by_name = {}
        self._load()

    @property
    def size(self):
        return len(self._by_name)

    def member_for(self, name):
        """The roster entry for an exact (case-insensitive) name, else None.

        Deliberately not fuzzy. 'Sean' is not 'Seanfreston', and a near-miss
        seats or pings the wrong person minutes before a live race.
        """
        key = _key(name)
        return self._by_name.get(key) if key else None

    def user_id_for(self, name):
        """Managed-user id, which is what a broadcast draft stores.

        The Discord id is for mentions. Passing one where the other is
        expected silently invites nobody.
        """
        member = self.member_for(name)
        return member.get('id') if member else None

    def discord_id_for(self, name):
        """Discord id for an exact (case-insensitive) name, else None.

        Deliberately not fuzzy. 'Sean' is not 'Seanfreston', and a near-miss
        pings the wrong person minutes before a live race.
        """
        member = self.member_for(name)
        return member.get('discordId') if member else None

    def mentions(self, names):
        """Return (rendered, ids) for a run of sheet names.

        Rendered entries are Discord mentions where the name resolved and the
        plain name where it did not, so an unknown commentator is still
        credited. Ids are what the caller must allow-list.
        """
        rendered = []
        ids = []
        for name in names:
            text = _clean(name)
            if not text:
                continue
            discord_id = self.discord_id_for(text)
            if discord_id:
                rendered.append('<@{}>'.format(discord_id))
                ids.append(discord_id)
            else:
                rendered.append(text)
        return rendered, ids

    async def refresh(self, url, token, requester=None):
        """Re-read the roster from the control plane. Never raises.

        Returns True only when a usable roster was adopted. Every failure
        path keeps whatever is already cached, because the control plane
        sleeps on idle and being unreachable is routine rather than
        exceptional.
        """
        if not url or not token:
            return False
        request = requester if requester is not None else aiohttp.request
        try:
            async with request(
                method='get',
                url=url,
                headers={'Authorization': 'Bearer {}'.format(token)},
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
            ) as response:
                if response.status != 200:
                    self._logger.warning(
                        'League crew roster fetch failed (HTTP %d); '
                        'keeping %d cached entries',
                        response.status, len(self._by_name),
                    )
                    return False
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, TypeError) as exc:
            self._logger.warning(
                'League crew roster unreachable (%s); keeping %d cached entries',
                type(exc).__name__, len(self._by_name),
            )
            return False
        members = payload.get('roster') if isinstance(payload, dict) else None
        return self.replace(members)

    def replace(self, members):
        """Adopt a freshly fetched roster and persist it."""
        parsed = {}
        for member in members or ():
            if not isinstance(member, dict):
                continue
            name = _clean(member.get('name'))
            discord_id = _clean(member.get('discordId'))
            user_id = _clean(member.get('id'))
            # Both ids required: a member who can be mentioned but not seated,
            # or seated but not mentioned, is half-resolved and worse than
            # absent - it would look resolvable right up to the failure.
            if not name or not discord_id or not user_id:
                continue
            parsed[_key(name)] = {'id': user_id, 'discordId': discord_id, 'name': name}
        if not parsed:
            # An empty payload is far likelier to be a broken response than a
            # league with no crew, and forgetting everyone turns every mention
            # into plain text with nothing to show why.
            self._logger.warning(
                'League crew roster came back empty; keeping %d cached entries',
                len(self._by_name),
            )
            return False
        self._by_name = parsed
        self._save()
        return True

    def _load(self):
        try:
            raw = self._cache_path.read_text(encoding='utf-8')
        except OSError:
            return
        try:
            cached = json.loads(raw)
        except ValueError:
            # A half-written cache must not stop the bot starting.
            self._logger.warning('League crew cache is unreadable; ignoring it')
            return
        if isinstance(cached, dict):
            self._by_name = {
                _key(name): value
                for name, value in cached.items()
                # A cache written by an older build stored a bare Discord id
                # string. Ignore those rather than half-loading them.
                if _key(name) and isinstance(value, dict)
                and _clean(value.get('id')) and _clean(value.get('discordId'))
            }

    def _save(self):
        try:
            ensure_parent_dir(self._cache_path)
            self._cache_path.write_text(
                json.dumps(self._by_name, indent=2, sort_keys=True), encoding='utf-8',
            )
        except OSError:
            # Losing the cache costs a stale-roster fallback, not a race.
            self._logger.warning('Could not write the League crew cache')
