"""Crew names from the always-on owner, cached for announcement continuity.

This cache never grants permissions. Unknown names remain plain text. Legacy
roster responses remain supported until the coordinated production cutover.
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


def booth_token_from_env(env):
    """A central read credential must never be sent to the control plane."""
    token = _clean(env.get('Z1RR_BOOTH_TOKEN'))
    if _clean(env.get('Z1RR_ROSTER_ENVIRONMENT')):
        roster_token = _clean(env.get('Z1RR_ROSTER_TOKEN'))
        if not roster_token or not _clean(env.get('Z1RR_ROSTER_URL')):
            raise ValueError('Central crew mode requires an explicit owner roster URL and token')
        if token == roster_token:
            raise ValueError('Owner and booth credentials must be separate')
        if not token and _clean(env.get('Z1RR_CONTROL_PLANE_URL')):
            raise ValueError('Central crew mode requires Z1RR_BOOTH_TOKEN for booth requests')
        return token
    return token or _clean(env.get('Z1RR_ROSTER_TOKEN'))


class CrewDirectory:
    """Name -> Discord id for everyone eligible to be named as crew."""

    def __init__(self, cache_path, logger, environment=None):
        if environment not in (None, '', 'production', 'staging'):
            raise ValueError('Invalid Crew roster environment')
        self._environment = environment or None
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
        """Re-read the roster from its configured owner. Never raises.

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
                headers={'Authorization': 'Bearer {}'.format(token),
                         **({'X-Z1RR-Environment': self._environment} if self._environment else {})},
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
        if self._environment or (isinstance(payload, dict) and 'schemaVersion' in payload):
            if (not isinstance(payload, dict) or payload.get('schemaVersion') != 1
                    or not self._environment or payload.get('environment') != self._environment
                    or payload.get('complete') is not True
                    or type(payload.get('revision')) is not int or payload['revision'] < 0
                    or not isinstance(members, list)):
                return False
            return self.replace(members, complete=True)
        return self.replace(members)

    def replace(self, members, complete=False):
        """Adopt a freshly fetched roster and persist it."""
        parsed = {}
        for member in members or ():
            if not isinstance(member, dict):
                if complete:
                    return False
                continue
            name = _clean(member.get('name'))
            discord_id = _clean(member.get('discordId'))
            user_id = _clean(member.get('id'))
            # Both ids required: a member who can be mentioned but not seated,
            # or seated but not mentioned, is half-resolved and worse than
            # absent - it would look resolvable right up to the failure.
            if not name or not discord_id or not user_id:
                if complete:
                    return False
                continue
            aliases = member.get('aliases', [])
            if not isinstance(aliases, list) or any(not _clean(a) for a in aliases):
                return False
            entry = {'id': user_id, 'discordId': discord_id, 'name': name}
            for alias in [name] + aliases:
                key = _key(alias)
                if key in parsed and (parsed[key]['id'], parsed[key]['discordId']) != (user_id, discord_id):
                    return False
                parsed[key] = entry
        if not parsed and not complete:
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
            if self._environment:
                if cached.get('schemaVersion') != 1 or cached.get('environment') != self._environment or not isinstance(cached.get('byName'), dict):
                    return
                cached = cached['byName']
            elif 'schemaVersion' in cached:
                return
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
                json.dumps({'schemaVersion': 1, 'environment': self._environment, 'byName': self._by_name}
                           if self._environment else self._by_name, indent=2, sort_keys=True), encoding='utf-8',
            )
        except OSError:
            # Losing the cache costs a stale-roster fallback, not a race.
            self._logger.warning('Could not write the League crew cache')
