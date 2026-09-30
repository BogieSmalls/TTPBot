"""Talking to the bracket engine.

The engine is the only thing that writes a tournament. This is a client of it,
not a second writer: nothing here opens `tournament.json`, and nothing here has
an opinion about who won or who advances. It asks, and it reports what it was
told.

The reason that matters is `times`. The Schedule tab owns when a race is, and the
engine's copy is a *mirror* -- so a mirror write that fails is a nuisance, and a
mirror write that is believed when it did not happen is a room at a time nobody
agreed to. Which is why there are three outcomes here rather than two:

    recorded          the engine said yes
    not recorded      the engine said no, and why -- a 4xx, a refusal
    could not confirm the answer was lost: a timeout, a dropped socket, a 5xx

The third is not a failure. A lost answer is settled by *reading back*, never by
sending again, because the send may have landed. `mirror_time` does that read
itself; everything else hands the outcome to the caller, which is the only one
that knows whether asking twice is safe.
"""

import asyncio
from dataclasses import dataclass
import json as jsonlib
from typing import Optional

import aiohttp

#: Where the engine listens on the relay. 3005 is the lifecycle relay and 3006 the
#: broadcast control plane, so the tournament engine took the next one.
DEFAULT_ENGINE_URL = 'http://127.0.0.1:3007'

#: Long enough for a write that fsyncs, short enough that a tick is not held up.
TIMEOUT_SECONDS = 10

RECORDED = 'recorded'
NOT_RECORDED = 'not-recorded'
UNCONFIRMED = 'could-not-confirm'


class EngineUnreachable(Exception):
    """The engine could not be asked, or its answer was lost.

    Carries the outcome so a caller can tell "it said no" from "nobody knows",
    because those want opposite handling: the first is final, and the second is
    settled by looking.
    """

    def __init__(self, message, outcome=UNCONFIRMED, status=None):
        super().__init__(message)
        self.outcome = outcome
        self.status = status


@dataclass(frozen=True)
class Written:
    """What came back from a write, and whether it is believed."""

    outcome: str
    revision: Optional[int] = None
    detail: Optional[str] = None
    answer: dict = None

    @property
    def ok(self):
        return self.outcome == RECORDED


class AutumnEngine:
    """The engine, as the Autumn runner needs it.

    Deliberately narrow. The runner reads the draw and mirrors a time; it does
    not record results, award matches or redraw anything -- those are an
    operator's commands, run by somebody who can look at the bracket. Adding them
    here would put a second set of hands on the tournament during a race night.
    """

    def __init__(self, url=None, token=None, event='autumn', logger=None,
                 session_factory=None):
        self._url = (url or DEFAULT_ENGINE_URL).rstrip('/')
        self._token = token
        self._event = event
        self._log = logger
        # Injected in tests. Nothing here builds a session per call in
        # production either -- aiohttp.request does that -- but a test needs to
        # answer without a socket.
        self._session_factory = session_factory

    @property
    def event(self):
        return self._event

    # -- asking ------------------------------------------------------------

    async def _get(self, path, authorized=False):
        headers = {'accept': 'application/json'}
        if authorized:
            if not self._token:
                raise EngineUnreachable('the engine needs a token', NOT_RECORDED)
            headers['authorization'] = 'Bearer {}'.format(self._token)
        try:
            async with self._request('GET', path, headers=headers) as response:
                body = await response.text()
                if response.status != 200:
                    raise EngineUnreachable(
                        'engine said {} for {}: {}'.format(
                            response.status, path, body.strip()[:200]),
                        # A 4xx is an answer; a 5xx is the engine failing to give
                        # one, and only the second is worth reading back for.
                        NOT_RECORDED if response.status < 500 else UNCONFIRMED,
                        response.status,
                    )
                return jsonlib.loads(body)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise EngineUnreachable(
                'engine unreachable at {}: {}'.format(path, exc)) from exc
        except ValueError as exc:
            # A 200 that is not JSON. Not an empty draw -- something else is
            # answering on that port, and pretending it is a draw would draw a
            # bracket nobody is racing.
            raise EngineUnreachable(
                'engine answer for {} was not JSON: {}'.format(path, exc)) from exc

    def _request(self, method, path, headers=None, json=None):
        if self._session_factory:
            return self._session_factory(
                method, self._url + path, headers=headers, json=json)
        return aiohttp.request(
            method,
            self._url + path,
            headers=headers,
            json=json,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        )

    async def draw(self):
        """The public draw: seats, matches, and the alias map.

        The aliases come from here rather than being kept in this repo so that
        who somebody is has one answer. Two copies would eventually disagree and
        this would be the quieter one.
        """
        return await self._get(
            '/tournament/public.json?event={}'.format(self._event))

    async def matches(self):
        """The draw's matches keyed by id, which is what the matcher takes."""
        drawn = await self.draw()
        return {match['id']: match for match in drawn.get('matches', [])}

    async def state(self):
        """The whole document, through the writer's queue. Needs the token."""
        return await self._get(
            '/tournament/state?event={}'.format(self._event), authorized=True)

    # -- telling -----------------------------------------------------------

    async def _post(self, operation, payload):
        if not self._token:
            return Written(NOT_RECORDED, detail='the engine needs a token')
        headers = {
            'authorization': 'Bearer {}'.format(self._token),
            'accept': 'application/json',
        }
        body = dict(payload)
        body['event'] = self._event
        try:
            async with self._request(
                'POST', '/tournament/{}'.format(operation),
                headers=headers, json=body,
            ) as response:
                text = await response.text()
                if response.status == 200:
                    answer = jsonlib.loads(text)
                    return Written(
                        RECORDED, revision=answer.get('revision'), answer=answer)
                detail = text.strip()[:200]
                if response.status < 500:
                    # The engine looked at it and said no. Asking again with the
                    # same request gets the same no.
                    return Written(NOT_RECORDED, detail=detail)
                return Written(UNCONFIRMED, detail=detail)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # The request may have landed. This is the case that must never be
            # retried blind.
            return Written(UNCONFIRMED, detail=str(exc))
        except ValueError as exc:
            return Written(UNCONFIRMED, detail='answer was not JSON: {}'.format(exc))

    async def mirror_time(self, match_id, at):
        """Tell the engine when a match is, as the sheet has it.

        The sheet is authoritative, so this is a mirror and never a source: it is
        sent *after* the sheet says so, and a disagreement is resolved by sending
        the sheet's answer again rather than by reading the engine's.

        An unconfirmed send is settled by reading back, because sending again
        could be the second of two writes when the first one landed. If the
        read-back shows the time already recorded, that is a success -- it just
        arrived without an answer.
        """
        when = at.isoformat() if hasattr(at, 'isoformat') else str(at)
        written = await self._post('time', {'matchId': match_id, 'at': when})
        if written.outcome != UNCONFIRMED:
            return written

        if self._log:
            self._log.warning(
                'Autumn: the engine did not answer the time for %s; reading back',
                match_id)
        try:
            state = await self.state()
        except EngineUnreachable as exc:
            # The read failed too, so nobody knows. Said plainly rather than
            # guessed, because the guess is wrong exactly when it costs most.
            return Written(UNCONFIRMED, detail='read-back failed: {}'.format(exc))

        document = state.get('document') or {}
        recorded = (document.get('times') or {}).get(match_id)
        if recorded and recorded.get('at') == when:
            return Written(
                RECORDED,
                revision=document.get('revision'),
                detail='confirmed by read-back',
            )

        # A read-back is only ever evidence *for*. Finding nothing does not mean
        # the write was refused -- a request that timed out may still be queued
        # behind another write and land a second from now, and calling that
        # `not-recorded` is a claim the read cannot support. So this stays
        # unconfirmed, which is the answer that makes a caller look again rather
        # than conclude.
        #
        # Which is safe here precisely because a mirror write is idempotent:
        # sending the sheet's time again next tick costs nothing if the first one
        # lands, and fixes it if it did not.
        return Written(
            UNCONFIRMED,
            detail=(
                'the engine has no such time recorded yet; it holds {!r}'.format(
                    recorded.get('at')) if recorded
                else 'the engine has no time recorded for this match yet'
            ),
        )

    async def claim_thread(self, match_id, by):
        """Claim the right to create a match's thread. See the engine's writer."""
        return await self._post('claimThread', {'matchId': match_id, 'by': by})

    async def record_thread(self, match_id, thread_id):
        """Record a thread that exists, so it is never created twice."""
        return await self._post(
            'thread', {'matchId': match_id, 'threadId': str(thread_id)})


def engine_from_env(env, event='autumn', logger=None):
    """An engine client from the environment, or None when it is not configured.

    None rather than a broken client: a relay without the token should run the
    League and say nothing about Autumn, not fail every tick.
    """
    token = (env.get('Z1RR_ENGINE_TOKEN') or '').strip()
    if not token:
        if logger:
            logger.info(
                'Autumn: no Z1RR_ENGINE_TOKEN, so the tournament runner stays off')
        return None
    return AutumnEngine(
        url=(env.get('Z1RR_ENGINE_URL') or '').strip() or DEFAULT_ENGINE_URL,
        token=token,
        event=event,
        logger=logger,
    )
