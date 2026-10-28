"""Read the Sheet-owned season from its single database projection."""
import asyncio
import csv
import io
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs
import aiohttp
from .matchups import parse_matchups
from .schedule import parse_schedule, schedule_is_readable

SOURCE_ID = '1MEyO03Wib6iyH7-75e-orh2K75AATwEoJB9HlTe9VgM'
MAX_AGE_SECONDS = 90

def csv_text(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()

class LeagueDatabaseSource:
    def __init__(self, url, token, environment, roster, logger, require_central_staffing=False):
        if not url or not token or environment not in ('production', 'staging', 'test'):
            raise ValueError('League database URL, reader token and environment required')
        self.url, self.token, self.environment = url, token, environment
        self.roster, self.logger = roster, logger
        self.require_central_staffing = require_central_staffing
        self._snapshot = None
        self._read_at = None

    async def _read(self):
        async with aiohttp.request('get', self.url, headers={
            'Authorization': 'Bearer ' + self.token,
            'x-z1rr-environment': self.environment,
        }, timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status != 200:
                raise ValueError('League database HTTP {}'.format(response.status))
            return await response.json()

    async def _load(self, now):
        if self._read_at is None or (now-self._read_at).total_seconds() >= 5:
            try:
                book = await self._read()
                if book.get('environment') != self.environment or book.get('sourceId') != SOURCE_ID:
                    raise ValueError('League database source mismatch')
                if self.require_central_staffing and book.get('staffing', {}).get('authority') != 'data':
                    raise ValueError('Central League staffing has not synced')
                ids = {t['id'] for t in book['tables']}
                if not {2033319762, 264392304}.issubset(ids):
                    raise ValueError('League database tabs missing')
                datetime.fromisoformat(book['fetchedAt'].replace('Z', '+00:00'))
                self._snapshot, self._read_at = book, now
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError, OSError):
                self.logger.warning('League database read delayed; keeping the last verified snapshot')
        if self._snapshot is None:
            raise ValueError('League database has not synced')
        captured = datetime.fromisoformat(self._snapshot['fetchedAt'].replace('Z', '+00:00'))
        if not -60 <= (now-captured).total_seconds() <= MAX_AGE_SECONDS:
            raise ValueError('League sync is stale; holding new room actions')
        return self._snapshot

    @staticmethod
    def _rows(book, gid):
        return next(t['rows'] for t in book['tables'] if int(t['id']) == int(gid))

    async def races(self, now):
        try:
            book = await self._load(now)
            fixtures = parse_matchups(csv_text(self._rows(book, 264392304)), self.logger)
            text = csv_text(self._rows(book, 2033319762))
            if not schedule_is_readable(text):
                raise ValueError('League database schedule is unreadable')
            return parse_schedule(text, self.roster, self.logger, matchups=fixtures)
        except (ValueError, StopIteration, KeyError, TypeError) as exc:
            self.logger.error('League database schedule held: %s', exc)
            return []

    async def matchups(self):
        try:
            book = await self._load(datetime.now(timezone.utc))
            return parse_matchups(csv_text(self._rows(book, 264392304)), self.logger)
        except (ValueError, StopIteration, KeyError, TypeError):
            return None

    async def rows_for_url(self, url):
        try:
            gid = parse_qs(urlsplit(url).query)['gid'][0]
            book = await self._load(datetime.now(timezone.utc))
            return self._rows(book, gid)
        except (ValueError, StopIteration, KeyError, TypeError):
            return None

def configured_source(env, roster, logger):
    url = env.get('LEAGUE_READ_MODEL_URL', '').strip()
    if not url:
        if env.get('LEAGUE_CENTRAL_STAFFING') == '1':
            raise ValueError('Central League staffing requires the database source')
        return None
    return LeagueDatabaseSource(url, env.get('LEAGUE_READ_MODEL_TOKEN', ''),
                                env.get('LEAGUE_READ_MODEL_ENVIRONMENT', ''), roster, logger,
                                require_central_staffing=env.get('LEAGUE_CENTRAL_STAFFING') == '1')
