"""Fetching the Schedule tab, and the response that lies about being it.

The failure this exists for: Google answers 200 with an HTML sign-in page once a
tab stops being world-readable. Parsed, that is zero rows -- exactly what a
cleared schedule looks like -- and the two want opposite behavior. Believing the
sign-in page cancels every race on the tab.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import unittest

import aiohttp

from ttpbot.autumn.source import CACHE_MAX_AGE, AutumnSource

HEADER = 'Date,Time,Runner 1,Runner 2,,Comms 1,Comms 2,Tracker,,Channel'
ONE_ROW = HEADER + '\n10/02/2026,10:00 PM,(46) ISUMatt,(7) chessjerk,,,,,,'
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class Log(logging.Logger):
    def __init__(self):
        super().__init__('autumn-test')
        self.errors = []
        self.warnings = []
        self.infos = []

    def error(self, msg, *args, **kw):
        self.errors.append(msg % args if args else msg)

    def warning(self, msg, *args, **kw):
        self.warnings.append(msg % args if args else msg)

    def info(self, msg, *args, **kw):
        self.infos.append(msg % args if args else msg)


def source(*bodies):
    """A source whose fetches answer from a script."""
    log = Log()
    queue = list(bodies)

    async def fetch(url):
        if not queue:
            raise AssertionError('nothing scripted for a fetch')
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return AutumnSource('https://example/sheet.csv', log, fetch=fetch), log


def run(coro):
    return asyncio.run(coro)


class ReadingIt(unittest.TestCase):
    def test_a_good_fetch_is_parsed(self):
        it, _ = source(ONE_ROW)
        found = run(it.rows(NOW))
        self.assertTrue(found.readable)
        self.assertEqual(len(found.rows), 1)

    def test_an_empty_tab_is_a_real_empty_schedule(self):
        # Header only, which is what the tab holds today. Readable, no races --
        # so a runner should open nothing, not fall back to a cached copy.
        it, log = source(HEADER)
        found = run(it.rows(NOW))
        self.assertTrue(found.readable)
        self.assertEqual(found.rows, [])
        self.assertEqual(log.errors, [])

    def test_no_url_is_off_rather_than_broken(self):
        it = AutumnSource('', Log())
        self.assertFalse(it.configured)
        self.assertFalse(run(it.rows(NOW)).readable)


class WhenItCannotBeBelieved(unittest.TestCase):
    def test_a_sign_in_page_keeps_the_last_good_copy(self):
        it, log = source(ONE_ROW, '<html><body>Sign in</body></html>')
        run(it.rows(NOW))
        found = run(it.rows(NOW + timedelta(minutes=1)))
        self.assertEqual(len(found.rows), 1, 'the cached race is still there')
        self.assertTrue(any('sign-in' in e for e in log.errors))

    def test_a_sign_in_page_before_any_good_read_is_not_a_schedule(self):
        # Nothing cached, so nothing is claimed. Not an empty schedule.
        it, _ = source('<html>Sign in</html>')
        self.assertFalse(run(it.rows(NOW)).readable)

    def test_a_failed_fetch_keeps_the_last_good_copy(self):
        it, log = source(ONE_ROW, aiohttp.ClientError('HTTP 500'))
        run(it.rows(NOW))
        found = run(it.rows(NOW + timedelta(minutes=1)))
        self.assertEqual(len(found.rows), 1)
        self.assertTrue(any('could not be fetched' in e for e in log.errors))

    def test_a_timeout_keeps_it_too(self):
        it, _ = source(ONE_ROW, asyncio.TimeoutError())
        run(it.rows(NOW))
        self.assertEqual(len(run(it.rows(NOW + timedelta(minutes=1))).rows), 1)

    def test_a_stale_copy_is_abandoned_once_it_is_too_old(self):
        # A schedule from two days ago will happily open a room for a race that
        # has since been moved, and nobody is watching the log by then.
        it, log = source(ONE_ROW, aiohttp.ClientError('still down'))
        run(it.rows(NOW))
        found = run(it.rows(NOW + CACHE_MAX_AGE + timedelta(minutes=1)))
        self.assertFalse(found.readable)
        self.assertEqual(found.rows, [])
        self.assertTrue(any('too long' in e for e in log.errors))

    def test_it_complains_once_not_every_tick(self):
        it, log = source(ONE_ROW, *(['<html>Sign in</html>'] * 5))
        run(it.rows(NOW))
        for minute in range(1, 6):
            run(it.rows(NOW + timedelta(minutes=minute)))
        self.assertEqual(len(log.errors), 1, log.errors)

    def test_it_says_so_when_the_tab_comes_back(self):
        it, log = source(ONE_ROW, '<html>Sign in</html>', ONE_ROW)
        run(it.rows(NOW))
        run(it.rows(NOW + timedelta(minutes=1)))
        run(it.rows(NOW + timedelta(minutes=2)))
        self.assertTrue(any('readable again' in i for i in log.infos))
        # And it would complain again next time, rather than staying quiet.
        self.assertFalse(it._complained)


if __name__ == '__main__':
    unittest.main()
