"""Each channel's coming races, so a booth booking can say what follows it.

The control plane sizes a booth's encoder for its whole back-to-back block: a
head-to-head followed on the same channel by a four-racer race should start
on the large encoder, so the booth can roll into that race without a stop.
TTPBot is the only scheduler and the only one that knows the night's later
races across League, the tournaments and TTP Nightly, so each scheduler
publishes its own and every booking carries the merged list for its channel
(Bogie, 2026-11-11).

The slate is advisory. A booking is the same booking whatever its slate says,
so it never decides whether a request is resent (`booking_signature`).
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

#: The control plane refuses a longer slate; a race night never comes close.
MAX_SLATE_ENTRIES = 24
#: How far past the booked start a booking looks for its block's races.
SLATE_HORIZON = timedelta(hours=12)
#: Request keys that never make a booking a different booking.
_ADVISORY_KEYS = ('slate', 'prepareOnly')


@dataclass(frozen=True)
class SlateEntry:
    channel: str
    start: datetime
    racers: int


def _utc_iso(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


class ChannelSlate:
    """The latest races each scheduler has published, merged per channel."""

    def __init__(self):
        self._by_source = {}

    def publish(self, source: str, entries: Iterable[SlateEntry]) -> None:
        """Replace this source's races; other sources keep theirs."""
        self._by_source[source] = tuple(entry for entry in entries if entry.channel)

    def for_booking(self, channel: str, after: datetime, *, horizon: timedelta = SLATE_HORIZON) -> list:
        """The channel's races starting after `after` and within `horizon`, earliest first."""
        wanted = (channel or '').casefold()
        until = after + horizon
        races = sorted(
            (entry for entries in self._by_source.values() for entry in entries
             if entry.channel.casefold() == wanted and after < entry.start <= until),
            key=lambda entry: entry.start)
        return [{'scheduledAt': _utc_iso(entry.start), 'racers': max(2, min(4, int(entry.racers)))}
                for entry in races[:MAX_SLATE_ENTRIES]]


def booking_signature(payload: dict) -> str:
    """What makes two booth requests the same request: everything but the advisory keys."""
    return json.dumps({key: value for key, value in payload.items() if key not in _ADVISORY_KEYS}, sort_keys=True)
