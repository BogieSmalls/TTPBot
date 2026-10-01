"""The tournament adapter to the League's proven crew and booth transport."""
import json
from ..league.booth import BoothOutcome, request_booth
from .broadcast_request import build_broadcast_request


class AutumnBooths:
    def __init__(self, *, engine, crew, logger, base_url, token, edition, wake=None, roster_url=None):
        self.engine = engine
        self.crew = crew
        self.logger = logger
        self.base_url = base_url
        self.token = token
        self.edition = edition
        self.wake = wake
        self.roster_url = roster_url or base_url.rstrip('/') + '/internal/relay/league/roster'
        self._outcomes = {}

    async def prepare(self, race, channel):
        if self.wake is not None:
            await self.wake(race, channel)
        if not await self.crew.refresh(self.roster_url, self.token):
            raise RuntimeError('the control plane crew roster has not refreshed yet')

    async def request(self, race, row, room_url):
        if row is None or not row.channel:
            return BoothOutcome()
        try:
            drawn = await self.engine.draw()
            match = next((m for m in drawn.get('matches', []) if m.get('id') == race.match_id), None)
            if (not match or match.get('state') != 'ready'
                    or {match.get('a'), match.get('b')} != {race.runner_one, race.runner_two}):
                self.logger.warning('Autumn %s is no longer the ready matchup; no booth requested', race.match_id)
                return BoothOutcome()
            state = await self.engine.state()
            payload = build_broadcast_request(race, row, room_url, state.get('document') or {}, self.crew,
                                              self.logger, edition=self.edition, number=match.get('number'))
            if payload is None:
                return BoothOutcome()
            signature = json.dumps(payload, sort_keys=True)
            held = self._outcomes.get(payload['requestKey'])
            if held and held[0] == signature:
                return held[1]
            outcome = await request_booth(payload, self.base_url, self.token, self.logger,
                                          endpoint='/internal/relay/tournament/broadcast', label='Autumn')
            if outcome.outcome in ('staged', 'continuation'):
                self._outcomes[payload['requestKey']] = (signature, outcome)
            return outcome
        except Exception:
            self.logger.warning('Autumn booth request failed for %s; room and invites are unaffected',
                                race.match_id, exc_info=True)
            return BoothOutcome()
