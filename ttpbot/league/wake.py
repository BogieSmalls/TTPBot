"""Wake the production control plane before a League booth is needed.

The relay already owns this: `POST /api/wake` reads the instance state, starts
it only if needed, and **waits for readiness** rather than for the instance to
report RUNNING. That last part is what makes a T-35 wake safe for a T-30 booth
rather than merely hopeful.

TTPBot runs on the same host as the relay, so this is a loopback call - no OCI
credentials here, and no second implementation of the lifecycle.
"""

import aiohttp

#: Generous, because the call blocks until the control plane is actually ready
#: rather than returning as soon as the instance is starting.
WAKE_TIMEOUT_SECONDS = 180


async def wake_control_plane(relay_url, logger, target='production', requester=None):
    """Ask the relay to wake the control plane. Returns True when it is ready.

    Raises on failure so the caller can decide to retry; the scheduler treats
    a failure as "try again on the next tick", which is safe because the
    endpoint is idempotent.
    """
    request = requester if requester is not None else aiohttp.request
    url = relay_url.rstrip('/') + '/api/wake'
    async with request(
        method='post',
        url=url,
        json={'target': target},
        timeout=aiohttp.ClientTimeout(total=WAKE_TIMEOUT_SECONDS),
    ) as response:
        if response.status != 200:
            body = await _safe_text(response)
            raise RuntimeError(
                'relay wake returned {}{}'.format(response.status, ': ' + body if body else ''),
            )
        logger.info('League wake: %s control plane is ready', target)
        return True


async def _safe_text(response):
    try:
        return (await response.text()).strip()[:200]
    except Exception:  # noqa: BLE001 - a failure body is diagnostic only
        return ''
