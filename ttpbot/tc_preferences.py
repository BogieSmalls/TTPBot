"""TC qualifier chat choices, saved by the portal before offers start."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import aiohttp

from .config import TIMEZONE


class TcPreferences:
    def __init__(self, *, portal_url, token, owner_url, reader_token, environment,
                 edition, rooms, requester=None, now=None):
        parsed = urlsplit(portal_url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('TC portal URL must be loopback HTTP')
        self.portal_url = portal_url.rstrip('/')
        self.token = token
        self.owner_url = owner_url.rstrip('/')
        self.reader_token = reader_token
        self.environment = environment
        self.edition_id = 'ttp-nightly:' + edition
        self.rooms = rooms
        self.requester = requester
        self.now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_env(cls, env, rooms):
        if not env.get('Z1RR_TC_CHAT_TOKEN'):
            return None
        required = ['TTPBOT_PORTAL_URL', 'COMPETITIONS_URL', 'COMPETITIONS_READER_TOKEN',
                    'COMPETITIONS_ENVIRONMENT', 'TTPBOT_NIGHTLY_EDITION']
        if any(not env.get(key) for key in required):
            raise ValueError('TC chat choices need portal and competitions reader settings')
        return cls(portal_url=env['TTPBOT_PORTAL_URL'], token=env['Z1RR_TC_CHAT_TOKEN'],
                   owner_url=env['COMPETITIONS_URL'], reader_token=env['COMPETITIONS_READER_TOKEN'],
                   environment=env['COMPETITIONS_ENVIRONMENT'], edition=env['TTPBOT_NIGHTLY_EDITION'], rooms=rooms)

    async def _request(self, method, url, token, *, body=None, owner=False):
        headers = {'Authorization':'Bearer ' + token}
        if owner:
            headers['x-z1rr-environment'] = self.environment
        async with (self.requester or aiohttp.request)(
                method=method, url=url, headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError('TC choice service HTTP {}'.format(response.status))
            return await response.json()

    async def record(self, room, message, declined):
        # Only a room this scheduler actually opened; never infer from a room title.
        race_time = next((datetime.fromisoformat(at) for at, url in self.rooms().items()
                          if urlsplit(url).path.strip('/') == room.strip('/')), None)
        now = self.now()
        if race_time is None or not race_time - timedelta(hours=1) <= now <= race_time + timedelta(hours=6):
            return {'error':'outside-qualifiers'}
        user = message.get('user') or {}
        if not user.get('id') or not user.get('name') or not message.get('id') or not message.get('posted_at'):
            return {'error':'missing-identity'}
        snapshot = await self._request('GET', self.owner_url + '/v1/competitions/snapshot', self.reader_token, owner=True)
        times = sorted({datetime.strptime(g['matchId'], 'N%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
                        for g in snapshot['games'] if g.get('editionId') == self.edition_id
                        and 'corto-qualifier' in g.get('associations', [])})
        # Same grouping as the Discord qualifier summary: consecutive tags at most six hours apart.
        nights = []
        for at in times:
            if nights and at - nights[-1][-1] <= timedelta(hours=6):
                nights[-1].append(at)
            else:
                nights.append([at])
        night = next((n for n in nights if race_time in n), None)
        if night is None:
            return {'error':'outside-qualifiers'}
        payload = {'racetimeId':user['id'], 'name':user['name'], 'declined':declined,
                   'messageId':str(message['id']), 'postedAt':message['posted_at'],
                   'nightKey':night[0].astimezone(TIMEZONE).date().isoformat(),
                   'firstRaceAt':night[0].isoformat(), 'lastRaceAt':night[-1].isoformat()}
        return await self._request('POST', self.portal_url + '/internal/tc/preference', self.token, body=payload)
