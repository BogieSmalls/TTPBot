"""Tournament destinations saved by Council, read before new announcements."""
import re
import aiohttp
from . import rehearsal


class TournamentChannels:
    def __init__(self, env, fetch=None):
        self.env, self.fetch = env, fetch
        self.last = None

    async def races(self, event, edition):
        if rehearsal.enabled(self.env):
            return None  # Rehearsal keeps its explicitly allowlisted channels.
        env = self.env
        if not self.fetch and not (env.get('COMPETITIONS_URL') and env.get('COMPETITIONS_READER_TOKEN')):
            return None
        try:
            if self.fetch:
                value = await self.fetch()
            else:
                async with aiohttp.request('GET', env['COMPETITIONS_URL'].rstrip('/') + '/v1/competitions/discord-channels',
                        headers={'Authorization': 'Bearer ' + env['COMPETITIONS_READER_TOKEN'],
                                 'x-z1rr-environment': env['COMPETITIONS_ENVIRONMENT']},
                        timeout=aiohttp.ClientTimeout(total=8)) as response:
                    if response.status != 200:
                        raise RuntimeError('Tournament channels HTTP {}'.format(response.status))
                    value = await response.json()
            if not isinstance(value.get('entries'), list) or any(not isinstance(row, dict)
                    or not isinstance(row.get('event'), str) or not isinstance(row.get('edition'), str)
                    or not re.fullmatch(r'\d{17,20}', str(row.get('races', ''))) for row in value['entries']):
                raise ValueError('Invalid tournament channel destinations')
            self.last = value
        except Exception:
            if self.last is None:
                raise  # Retry later instead of guessing where to post.
        return next((row['races'] for row in self.last['entries']
                     if row['event'] == event and row['edition'] == str(edition)), None)
