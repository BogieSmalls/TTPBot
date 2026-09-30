"""One Autumn match through the real adapters.

The adapters are the real modules -- `create_autumn_room`, `invite_racers`,
`send_autumn_announcement` -- driven against fake HTTP endpoints rather than
fakes standing in for the adapters themselves. So the request shapes, the status
handling and the recovery are all exercised; only the sockets are simulated.

The contract under test throughout: **falsey means it definitely did not
happen.** A timeout, a 5xx or an unreadable answer arrives after the request was
already on its way, so the room may exist, and calling that "not created" is how
a match gets two rooms.
"""

import asyncio
from datetime import datetime, timedelta
import logging
import unittest

import aiohttp

from ttpbot.autumn.announce import build_announcement
from ttpbot.autumn.rooms import UNCERTAIN_RACE, create_autumn_room, room_title
from ttpbot.autumn.matching import RaceIdentity, Resolved
from ttpbot.config import TIMEZONE
from ttpbot.provider import RacetimeProvider

START = datetime(2026, 10, 2, 22, 0, tzinfo=TIMEZONE)
ROOM_PATH = '/z1r/fancy-mario-1234'
ROOM_URL = 'https://racetime.gg' + ROOM_PATH


def provider():
    return RacetimeProvider('https://racetime.gg', 'z1r')


def race(match_id='W1-1', one='ISUMatt', two='chessjerk'):
    return Resolved(
        identity=RaceIdentity(event='autumn', match_id=match_id),
        match_id=match_id, at=START, runner_one=one, runner_two=two,
    )


class Log(logging.Logger):
    def __init__(self):
        super().__init__('autumn')
        self.errors = []
        self.warnings = []
        self.infos = []

    def error(self, msg, *args, **kw):
        self.errors.append(msg % args if args else msg)

    def warning(self, msg, *args, **kw):
        self.warnings.append(msg % args if args else msg)

    def info(self, msg, *args, **kw):
        self.infos.append(msg % args if args else msg)


class Response:
    def __init__(self, status=200, headers=None, json=None, text='',
                 raises=None, json_raises=None):
        self.status = status
        self.headers = headers or {}
        self._json = json
        self._text = text
        self._raises = raises
        self._json_raises = json_raises

    async def json(self, content_type=None):
        if self._json_raises:
            raise self._json_raises
        return self._json

    async def text(self):
        return self._text

    async def __aenter__(self):
        if self._raises:
            raise self._raises
        return self

    async def __aexit__(self, *exc):
        return False


class Endpoint:
    """A fake racetime/Discord, answering from a script and recording calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method=None, url=None, headers=None, data=None, json=None,
                 timeout=None):
        self.calls.append({
            'method': method, 'url': url, 'headers': headers or {},
            'data': data, 'json': json,
        })
        if not self.answers:
            raise AssertionError('nothing scripted for {} {}'.format(method, url))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            return Response(raises=answer)
        return answer


def run(coro):
    return asyncio.run(coro)


class CreatingTheRoom(unittest.TestCase):
    def setUp(self):
        self.log = Log()

    def test_a_created_room_comes_back_as_its_url(self):
        endpoint = Endpoint(Response(201, headers={'Location': ROOM_PATH}))
        url = run(create_autumn_room(
            race(), provider(), 'token', self.log, requester=endpoint))
        self.assertEqual(url, ROOM_URL)

        sent, = endpoint.calls
        self.assertEqual(sent['method'], 'post')
        self.assertTrue(sent['url'].endswith('/o/z1r/startrace'))
        self.assertEqual(sent['headers']['Authorization'], 'Bearer token')
        # The match id is in the room's own info, because that is what recovery
        # reads. A pair of names is not enough: the grand final and its reset are
        # the same two people.
        self.assertIn('[W1-1]', sent['data']['info_user'])
        self.assertIn('ISUMatt vs chessjerk', sent['data']['info_user'])
        # info_bot is left clear: `setinfo` overwrites it, so recovery must not
        # depend on a field somebody else owns.
        self.assertNotIn('info_bot', sent['data'])

    def test_the_grand_final_and_its_reset_have_different_titles(self):
        # Which is the whole point of putting the match id in there.
        self.assertNotEqual(
            room_title(race('GF-1', 'Bogie', 'Merks'), 'Grand Final'),
            room_title(race('GF-2', 'Bogie', 'Merks'), 'Grand Final Reset'),
        )

    def test_a_rejection_is_a_definite_no(self):
        # A 422 is racetime having looked at the request and refused it. Nothing
        # was created, so a later tick may try again.
        endpoint = Endpoint(Response(422, text='bad goal'))
        self.assertIsNone(run(create_autumn_room(
            race(), provider(), 'token', self.log, requester=endpoint)))
        self.assertTrue(any('nothing was' in e for e in self.log.errors))

    def test_no_token_is_a_definite_no(self):
        endpoint = Endpoint()
        self.assertIsNone(run(create_autumn_room(
            race(), provider(), None, self.log, requester=endpoint)))
        self.assertEqual(endpoint.calls, [], 'nothing should have been sent')

    def test_a_timeout_looks_for_the_room_and_finds_it(self):
        # Read, never re-POST. A retry is the one thing that turns "maybe a room"
        # into "definitely two rooms".
        title = room_title(race())
        endpoint = Endpoint(
            asyncio.TimeoutError(),
            Response(200, json={'current_races': [
                {'info_user': title, 'url': ROOM_PATH},
            ]}),
        )
        url = run(create_autumn_room(
            race(), provider(), 'token', self.log, requester=endpoint))
        self.assertEqual(url, ROOM_URL)
        self.assertEqual([c['method'] for c in endpoint.calls], ['post', 'get'])

    def test_a_timeout_that_finds_nothing_stays_uncertain(self):
        endpoint = Endpoint(
            asyncio.TimeoutError(),
            Response(200, json={'current_races': []}),
        )
        self.assertEqual(
            run(create_autumn_room(
                race(), provider(), 'token', self.log, requester=endpoint)),
            UNCERTAIN_RACE,
        )
        self.assertTrue(any('uncertain' in e for e in self.log.errors))

    def test_a_5xx_is_uncertain_not_a_refusal(self):
        # The POST reached racetime. A 500 is racetime failing to say what
        # happened to it, not evidence that nothing did. This is where the League's
        # version returns None, and that is the bug this does not copy.
        endpoint = Endpoint(
            Response(500, text='server error'),
            Response(200, json={'current_races': []}),
        )
        self.assertEqual(
            run(create_autumn_room(
                race(), provider(), 'token', self.log, requester=endpoint)),
            UNCERTAIN_RACE,
        )
        self.assertEqual([c['method'] for c in endpoint.calls], ['post', 'get'])

    def test_a_5xx_whose_room_is_found_comes_back_as_that_room(self):
        title = room_title(race())
        endpoint = Endpoint(
            Response(503),
            Response(200, json={'current_races': [
                {'info_user': title, 'name': 'z1r/fancy-mario-1234'},
            ]}),
        )
        self.assertEqual(
            run(create_autumn_room(
                race(), provider(), 'token', self.log, requester=endpoint)),
            ROOM_URL,
        )

    def test_a_dropped_connection_is_uncertain(self):
        endpoint = Endpoint(
            aiohttp.ClientConnectionError('reset'),
            Response(200, json={'current_races': []}),
        )
        self.assertEqual(
            run(create_autumn_room(
                race(), provider(), 'token', self.log, requester=endpoint)),
            UNCERTAIN_RACE,
        )

    def test_an_unreadable_recovery_stays_uncertain(self):
        # The read failed too, so nobody knows -- which is exactly what
        # UNCERTAIN_RACE is for.
        endpoint = Endpoint(
            asyncio.TimeoutError(),
            Response(200, json_raises=ValueError('not json')),
        )
        self.assertEqual(
            run(create_autumn_room(
                race(), provider(), 'token', self.log, requester=endpoint)),
            UNCERTAIN_RACE,
        )

    def test_two_rooms_claiming_the_match_is_left_to_a_person(self):
        # Picking one would hide the problem, and the problem needs somebody.
        title = room_title(race())
        endpoint = Endpoint(
            asyncio.TimeoutError(),
            Response(200, json={'current_races': [
                {'info_user': title, 'url': '/z1r/one'},
                {'info_user': title, 'url': '/z1r/two'},
            ]}),
        )
        self.assertEqual(
            run(create_autumn_room(
                race(), provider(), 'token', self.log, requester=endpoint)),
            UNCERTAIN_RACE,
        )
        self.assertTrue(any('more than one' in e for e in self.log.errors))

    def test_recovery_ignores_another_match_between_the_same_pair(self):
        # A losers-bracket rematch, or the final before its reset. Same two
        # people, different match, and its room is not this one's.
        endpoint = Endpoint(
            asyncio.TimeoutError(),
            Response(200, json={'current_races': [
                {'info_user': room_title(race('GF-1', 'Bogie', 'Merks')),
                 'url': '/z1r/the-final'},
            ]}),
        )
        found = run(create_autumn_room(
            race('GF-2', 'Bogie', 'Merks'), provider(), 'token', self.log,
            requester=endpoint))
        self.assertEqual(found, UNCERTAIN_RACE, 'the final is not the reset')


class Announcing(unittest.TestCase):
    def test_both_racers_are_pinged_when_they_are_known(self):
        body = build_announcement(
            race(), ROOM_URL,
            ids={'ISUMatt': '111', 'chessjerk': '222'}, label='Round of 64')
        self.assertIn('<@111>', body['content'])
        self.assertIn('<@222>', body['content'])
        self.assertIn('Round of 64', body['content'])
        self.assertIn(ROOM_URL, body['content'])
        self.assertEqual(body['allowed_mentions']['users'], ['111', '222'])
        # Nothing else can be pinged, including by a racer whose name contains
        # something that looks like a mention.
        self.assertEqual(body['allowed_mentions']['parse'], [])

    def test_an_unknown_racer_is_named_rather_than_left_out(self):
        body = build_announcement(race(), ROOM_URL, ids={'ISUMatt': '111'})
        self.assertIn('<@111>', body['content'])
        self.assertIn('chessjerk', body['content'])
        self.assertEqual(body['allowed_mentions']['users'], ['111'])

    def test_the_crew_rides_along_when_the_row_has_one(self):
        body = build_announcement(
            race(), ROOM_URL, crew=('Bogie', 'Merks'))
        self.assertIn('Restream crew: Bogie · Merks', body['content'])


HEADER = 'Date,Time,Runner 1,Runner 2,,Comms 1,Comms 2,Tracker,,Channel'


def schedule_row(start, one, two, channel=''):
    return '{},{},{},{},,{},,,,{}'.format(
        start.strftime('%m/%d/%Y'), start.strftime('%I:%M %p'), one, two,
        'Bogie' if channel else '', channel)


def sheet(*rows):
    return '\n'.join((HEADER,) + rows)


class OneMatchThroughTheRealAdapters(unittest.TestCase):
    """The scheduler driving the real adapters, over fake sockets.

    Everything between the Schedule tab and racetime is the production code path:
    the parser, the matcher, the bindings, the reservation, `create_autumn_room`,
    `invite_racers` and `build_announcement`. Only the HTTP is answered from a
    script.
    """

    def setUp(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from ttpbot.state import DestinationStateStore

        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.log = Log()
        self.store_for = lambda kind: DestinationStateStore(
            '{}.json'.format(kind), 'https://racetime.gg|z1r', kind,
            data_dir=self.root)

    def scheduler(self, csv_text, endpoint, matches=None):
        from ttpbot.autumn.engine import RECORDED, Written
        from ttpbot.autumn.schedule import parse_schedule
        from ttpbot.autumn.scheduler import AutumnScheduler

        matches = matches or {'W1-1': {
            'a': 'ISUMatt', 'b': 'chessjerk', 'state': 'ready'}}
        self.mirrored = []

        class Source:
            configured = True

            async def rows(inner, now):
                return parse_schedule(csv_text)

        class Engine:
            async def draw(inner):
                return {
                    'drawn': True, 'aliases': {},
                    'matches': [dict(m, id=i) for i, m in matches.items()],
                }

            async def mirror_time(inner, match_id, when):
                self.mirrored.append((match_id, when.isoformat()))
                return Written(RECORDED, revision=1)

            async def state(inner):
                return {'document': {'racers': {
                    'ISUMatt': '111', 'chessjerk': '222'}}}

        self.announced_bodies = []

        async def open_room(r, row):
            return await create_autumn_room(
                r, provider(), 'token', self.log, requester=endpoint)

        async def announce(r, row, url):
            self.announced_bodies.append(build_announcement(
                r, url, ids={'ISUMatt': '111', 'chessjerk': '222'},
                crew=tuple(getattr(row, 'crew', ()) or ())))

        return AutumnScheduler(
            source=Source(), engine=Engine(), logger=self.log,
            bindings_store=self.store_for('autumn_bindings'),
            created_store=self.store_for('autumn_created_races'),
            mirrored_store=self.store_for('autumn_mirrored_times'),
            announced_store=self.store_for('autumn_sent_webhooks'),
            open_room=open_room, announce=announce,
        )

    def test_the_whole_way_through(self):
        endpoint = Endpoint(Response(201, headers={'Location': ROOM_PATH}))
        it = self.scheduler(
            sheet(schedule_row(START, '(46) ISUMatt', '(7) chessjerk')), endpoint)

        run(it.tick(START - timedelta(minutes=30)))

        # The room was created, with the match id in its info.
        self.assertIn('[W1-1]', endpoint.calls[0]['data']['info_user'])
        self.assertEqual(it.created['autumn|W1-1'], ROOM_URL)
        # And it is joinable, because nothing invites anybody yet.
        self.assertEqual(endpoint.calls[0]['data']['invitational'], 'false')
        # The time reached the engine, and the announcement pinged both.
        self.assertEqual(self.mirrored, [('W1-1', START.isoformat())])
        body, = self.announced_bodies
        self.assertIn('<@111>', body['content'])
        self.assertIn(ROOM_URL, body['content'])
        self.assertTrue(it.announced['autumn|W1-1'])

        # And nothing happens again.
        run(it.tick(START - timedelta(minutes=20)))
        self.assertEqual(len(endpoint.calls), 1)
        self.assertEqual(len(self.announced_bodies), 1)

    def test_a_reschedule_keeps_the_room_and_moves_the_time(self):
        endpoint = Endpoint(Response(201, headers={'Location': ROOM_PATH}))
        it = self.scheduler(
            sheet(schedule_row(START, 'ISUMatt', 'chessjerk')), endpoint)
        run(it.tick(START - timedelta(minutes=30)))
        self.assertEqual(it.created['autumn|W1-1'], ROOM_URL)

        # The sheet now carries a second row for the same pair, two days on.
        moved_to = START + timedelta(days=2)
        quiet = Endpoint()
        moved = self.scheduler(
            sheet(schedule_row(START, 'ISUMatt', 'chessjerk'),
                  schedule_row(moved_to, 'ISUMatt', 'chessjerk')),
            quiet)
        run(moved.tick(moved_to - timedelta(minutes=30)))

        self.assertEqual(
            quiet.calls, [], 'a moved race must not get a second room')
        self.assertEqual(moved.created['autumn|W1-1'], ROOM_URL)
        # And the engine is told the new time, keyed by the match rather than by
        # the hour -- which is the reason the room survived at all.
        self.assertEqual(self.mirrored, [('W1-1', moved_to.isoformat())])

    def test_a_restart_after_an_uncertain_creation_opens_nothing(self):
        # The creation landed and the answer did not, and the room could not be
        # found. A restart must not try again.
        endpoint = Endpoint(
            asyncio.TimeoutError(),
            Response(200, json={'current_races': []}),
        )
        it = self.scheduler(
            sheet(schedule_row(START, 'ISUMatt', 'chessjerk')), endpoint)
        run(it.tick(START - timedelta(minutes=30)))
        self.assertEqual(it.created['autumn|W1-1'], UNCERTAIN_RACE)
        self.assertEqual(self.announced_bodies, [], 'nothing to announce')

        quiet = Endpoint()
        restarted = self.scheduler(
            sheet(schedule_row(START, 'ISUMatt', 'chessjerk')), quiet)
        run(restarted.tick(START - timedelta(minutes=25)))
        self.assertEqual(quiet.calls, [], 'a restart must not create a second room')
        self.assertTrue(any('never confirmed' in e for e in self.log.errors))

    def test_a_restart_after_a_good_creation_reuses_the_room(self):
        endpoint = Endpoint(Response(201, headers={'Location': ROOM_PATH}))
        it = self.scheduler(
            sheet(schedule_row(START, 'ISUMatt', 'chessjerk')), endpoint)
        run(it.tick(START - timedelta(minutes=30)))

        # A new process. The room must not be made again.
        again = Endpoint()
        restarted = self.scheduler(
            sheet(schedule_row(START, 'ISUMatt', 'chessjerk')), again)
        run(restarted.tick(START - timedelta(minutes=20)))

        self.assertEqual(
            again.calls, [], 'a restart must reuse the room it already made')
        self.assertEqual(restarted.created['autumn|W1-1'], ROOM_URL)
        # And the announcement guard is durable, so nobody is told twice. The
        # recorder is per-scheduler, so "not announced again" reads as empty here.
        self.assertEqual(self.announced_bodies, [])
        self.assertTrue(restarted.announced['autumn|W1-1'])


if __name__ == '__main__':
    unittest.main()
