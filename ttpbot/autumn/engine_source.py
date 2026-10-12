"""Engine-owned tournament clocks joined to Data crew by edition/match/game."""

from datetime import datetime
import aiohttp

from .schedule import Schedule, ScheduleRow, BadRow


class EngineScheduleSource:
    authority = 'engine'

    def __init__(self, event, edition, url, token, environment, logger, fetch=None):
        self.event, self.edition = event, edition
        self.url, self.token, self.environment = url, token, environment
        self.logger, self._fetch = logger, fetch

    @property
    def configured(self):
        return bool(self.url and self.token and self.edition and self.environment in ('test', 'staging', 'production'))

    async def _snapshot(self):
        if self._fetch:
            return await self._fetch()
        async with aiohttp.request('GET', self.url.rstrip('/') + '/v1/competitions/snapshot',
                                   headers={'Authorization': 'Bearer ' + self.token, 'x-z1rr-environment': self.environment},
                                   timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status != 200:
                raise ValueError('competition owner HTTP {}'.format(response.status))
            return await response.json()

    async def rows(self, now, *, document, drawn):
        if (not self.configured or document.get('scheduleAuthority') != 'engine'
                or document.get('edition') != self.edition or drawn.get('edition') != self.edition):
            self.logger.error('%s: engine schedule authority/edition is not ready', self.event)
            return Schedule(readable=False)
        try:
            snapshot = await self._snapshot()
            if snapshot.get('environment') != self.environment or not isinstance(snapshot.get('games'), list):
                raise ValueError('competition owner environment or snapshot mismatch')
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            self.logger.error('%s: competition crew unavailable: %s', self.event, exc)
            return Schedule(readable=False)
        matches = {m['id']: m for m in drawn.get('matches', [])}
        edition_id = self.event + ':' + self.edition
        crew = {(g['matchId'], g['gameNumber']): g for g in snapshot['games'] if g['editionId'] == edition_id}
        result = Schedule(observed_at=now)
        for match_id, clocks in document.get('gameTimes', {}).items():
            match = matches.get(match_id)
            if not match or document.get('results', {}).get(match_id):
                continue
            reset = match_id == 'GF-2' and not document.get('results', {}).get('GF-1')
            if match.get('state') in ('bye', 'void') or match.get('state') == 'not-needed' and not reset:
                continue
            pair = matches.get('GF-1', {}) if reset and not match.get('a') else match
            for number, clock in clocks.items():
                game = int(number)
                if clock.get('status') != 'scheduled' or any(g['game'] == game for g in document.get('games', {}).get(match_id, [])):
                    continue
                try:
                    at = datetime.fromisoformat(clock['at'].replace('Z', '+00:00'))
                    if at.tzinfo is None or not pair.get('a') or not pair.get('b'):
                        raise ValueError('time or participants are not confirmed')
                    booking = crew.get((match_id, game), {})
                    if booking and sorted(p['name'] for p in booking['participants']) != sorted((pair['a'], pair['b'])):
                        raise ValueError('crew reference participants disagree with bracket')
                    channels = booking.get('bookings', [])
                    if len(channels) > 1:
                        raise ValueError('multiple tournament channel bookings need reconciliation')
                    slots = booking.get('crew', {})
                    result.rows.append(ScheduleRow(at=at, runner_one=pair['a'], runner_two=pair['b'], match_id=match_id, game=game,
                        comms_one=slots.get('comms1', {}).get('name', ''), comms_two=slots.get('comms2', {}).get('name', ''),
                        tracker=slots.get('tracker', {}).get('name', ''), channel=channels[0]['channel'] if channels else ''))
                except (KeyError, TypeError, ValueError) as exc:
                    result.bad.append(BadRow(line=0, reason='{} Game {}: {}'.format(match_id, game, exc)))
        return result
