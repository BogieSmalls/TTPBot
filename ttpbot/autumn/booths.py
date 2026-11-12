"""The tournament adapter to the League's proven crew and booth transport."""
from ..booth_slate import booking_signature
from ..league.booth import BoothOutcome, request_booth, send_heads_up
from .broadcast_request import build_broadcast_request


class AutumnBooths:
    def __init__(self, *, engine, crew, logger, base_url, token, edition, wake=None, roster_url=None, roster_token=None,
                 slate=None):
        self.engine = engine
        #: Each channel's later races, shared with the other schedulers. None sends no slate.
        self.slate = slate
        self.crew = crew
        self.logger = logger
        self.base_url = base_url
        self.token = token
        self.roster_token = token if roster_token is None else roster_token
        self.edition = edition
        self.wake = wake
        self.roster_url = roster_url or base_url.rstrip('/') + '/internal/relay/league/roster'
        self._outcomes = {}
        #: Request keys whose T-35 heads-up has gone, apart from the booking outcomes.
        self._heads_up_sent = set()

    async def prepare(self, race, channel):
        if not (channel or '').strip():
            return
        if self.wake is not None:
            await self.wake(race, channel)
        if not await self.crew.refresh(self.roster_url, self.roster_token):
            raise RuntimeError('the crew roster has not refreshed yet')

    async def _payload(self, race, row, room_url, prepare_only=False):
        """The booking for a ready match, or None."""
        drawn = await self.engine.draw()
        match = next((m for m in drawn.get('matches', []) if m.get('id') == race.match_id), None)
        if (not match or match.get('state') != 'ready'
                or {match.get('a'), match.get('b')} != {race.runner_one, race.runner_two}):
            self.logger.warning('Autumn %s is no longer the ready matchup; no booth requested', race.match_id)
            return None
        state = await self.engine.state()
        payload = build_broadcast_request(race, row, room_url, state.get('document') or {}, self.crew,
                                          self.logger, edition=self.edition, number=match.get('number'),
                                          prepare_only=prepare_only)
        if payload is not None and self.slate is not None:
            payload = {**payload, 'slate': self.slate.for_booking(row.channel, row.at)}
        return payload

    async def heads_up(self, race, row):
        """The T-35 heads-up: the booking without a room, once per match game. Its answer is only logged."""
        if row is None or not (row.channel or '').strip():
            return None
        try:
            payload = await self._payload(race, row, None, prepare_only=True)
            if payload is None or payload['requestKey'] in self._heads_up_sent:
                return None
            self._heads_up_sent.add(payload['requestKey'])
            outcome = await send_heads_up(payload, self.base_url, self.token, self.logger,
                                          endpoint='/internal/relay/tournament/broadcast', label='Autumn')
            if outcome is not None and outcome.outcome is not None:
                self.logger.info('Autumn heads-up for %s: %s', race.match_id, outcome.outcome)
            return outcome
        except Exception:
            self.logger.warning('Autumn heads-up failed for %s', race.match_id, exc_info=True)
            return None

    async def request(self, race, row, room_url):
        if row is None or not (row.channel or '').strip():
            return BoothOutcome()
        try:
            payload = await self._payload(race, row, room_url)
            if payload is None:
                return BoothOutcome()
            # The slate is advisory: a booking whose later races changed is the same booking.
            signature = booking_signature(payload)
            held = self._outcomes.get(payload['requestKey'])
            if held and held[0] == signature:
                return held[1]
            outcome = await request_booth(payload, self.base_url, self.token, self.logger,
                                          endpoint='/internal/relay/tournament/broadcast', label='Autumn')
            if outcome.outcome in ('staged', 'continuation', 'next_race'):
                self._outcomes[payload['requestKey']] = (signature, outcome)
            return outcome
        except Exception:
            self.logger.warning('Autumn booth request failed for %s; room and invites are unaffected',
                                race.match_id, exc_info=True)
            return BoothOutcome()
