"""Ask the control plane to stand up a booth for a League race.

The endpoint is `POST /internal/relay/league/broadcast`, documented in
Z1RR.Restream's `docs/internal/platform-configuration.md`.

Its answer is what the Discord announcement is built from, which is why the
announcement is posted after this call rather than before: a continuation has
to carry the already-on-air warning, and only the control plane knows the
channel is already live. What this module must never do is fail loudly - the
room, the racer invites and the announcement all go out regardless.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional

import aiohttp

#: Bounded so a slow control plane cannot delay the announcement. The endpoint
#: does real work - it creates a draft and stages a booth - so this is
#: deliberately generous compared with the roster lookup.
BOOTH_TIMEOUT_SECONDS = 25


@dataclass(frozen=True)
class BoothOutcome:
    """What the control plane did, as far as we can tell.

    `outcome` is None whenever we do not know: unconfigured, refused,
    unreachable, or a replay whose stored result is missing. Every one of
    those means the announcement falls back to its normal wording.
    """

    outcome: Optional[str] = None
    broadcast_id: Optional[str] = None

    @property
    def is_continuation(self) -> bool:
        return self.outcome == 'continuation'


def _unwrap(payload):
    """Resolve `already_done` to the answer it is replaying.

    A retry has to announce what the original call actually did. Announcing
    "already done" would tell the crew nothing they can act on.
    """
    if not isinstance(payload, dict):
        return BoothOutcome()
    outcome = payload.get('outcome')
    if outcome == 'already_done':
        return _unwrap(payload.get('result'))
    if not isinstance(outcome, str):
        return BoothOutcome()
    broadcast_id = payload.get('broadcastId')
    return BoothOutcome(
        outcome=outcome,
        broadcast_id=broadcast_id if isinstance(broadcast_id, str) else None,
    )


async def request_booth(payload, base_url, token, logger, requester=None):
    """Call the endpoint. Never raises, and never blocks the announcement."""
    if not base_url or not token:
        return BoothOutcome()

    request = requester if requester is not None else aiohttp.request
    url = base_url.rstrip('/') + '/internal/relay/league/broadcast'
    try:
        async with request(
            method='post',
            url=url,
            json=payload,
            headers={'Authorization': 'Bearer {}'.format(token)},
            timeout=aiohttp.ClientTimeout(total=BOOTH_TIMEOUT_SECONDS),
        ) as response:
            if response.status != 200:
                body = await _safe_json(response)
                # A 409 is retryable at the far end; this tick simply does not
                # know an outcome, and the announcement carries on.
                logger.warning(
                    'League booth request refused (HTTP %s): %s', response.status, body,
                )
                return BoothOutcome()
            return _unwrap(await response.json())
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, TypeError) as exc:
        logger.warning(
            'League booth request failed (%s); the announcement still goes out',
            type(exc).__name__,
        )
        return BoothOutcome()


async def _safe_json(response):
    try:
        return await response.json()
    except Exception:  # noqa: BLE001 - a refusal body is diagnostic, never critical
        return None
