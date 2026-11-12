"""TTP Nightly booths (Bogie, 2026-10-24): Council staffs a nightly race; TTPBot opens its booth."""
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ttpbot.booth_slate import ChannelSlate, SlateEntry
from ttpbot.config import TIMEZONE
from ttpbot.nightly import NightlyBooths, booth_request, occurrence_key, occurrence_match_id

QUIET = logging.getLogger('test-nightly')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False

# Thursday Nov 5, 2026, 8 PM and 10 PM ET.
EIGHT = datetime(2026, 11, 5, 20, 0, tzinfo=TIMEZONE)
TEN = datetime(2026, 11, 5, 22, 0, tzinfo=TIMEZONE)
ROOM = 'https://racetime.gg/z1r/brave-goriya-1234'


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Requests:
    """Answers by URL; records each call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        for fragment, response in self.answers.items():
            if fragment in kwargs['url']:
                return response
        return _Response(404, {})


def staffed(channel='Z1Rracing5', qualifier=False):
    return {'crew': {'comms1': {'name': 'Alice'}, 'tracker': {'name': 'Bob'}}, 'bookings': [{'channel': channel}],
            'associations': ['corto-qualifier'] if qualifier else []}


def booths(requests, crew=None):
    return NightlyBooths(owner_url='https://data.example', owner_environment='staging', edition='2026',
                         actor='123456789012345678', reader_token='read', writer_token='write',
                         booth_url='https://cp.example', booth_token='booth',
                         crew=crew or Mock(user_id_for=lambda name: 'u-' + name), logger=QUIET,
                         requester=requests)


class IdentityTests(unittest.TestCase):
    def test_the_8_pm_and_10_pm_races_are_separate_occurrences(self):
        self.assertEqual(occurrence_match_id(EIGHT), 'N20261106T010000Z')
        self.assertEqual(occurrence_match_id(TEN), 'N20261106T030000Z')
        self.assertEqual(occurrence_key(EIGHT), 'ttp:2026-11-06T01:00:00.000Z')


class BoothRequestTests(unittest.TestCase):
    def test_a_booked_channel_without_assigned_crew_does_not_open_a_booth(self):
        self.assertIsNone(booth_request(EIGHT, {'crew': {}, 'bookings': [{'channel': 'TriforceTriplePlay'}]}, ROOM, None, QUIET))

    def test_tracker_two_alone_is_staffed_and_keeps_its_slot(self):
        occurrence = {'crew': {'tracker2': {'name': 'Trainee'}}, 'bookings': [{'channel': 'TriforceTriplePlay'}]}
        payload = booth_request(EIGHT, occurrence, ROOM, Mock(user_id_for=lambda n: 'u-' + n), QUIET)
        self.assertEqual(payload['commentatorUserIds'], [])
        self.assertIsNone(payload['trackerUserId'])
        self.assertEqual(payload['tracker2UserId'], 'u-Trainee')

    def test_a_staffed_nightly_asks_for_its_booth_with_crew_channel_and_room(self):
        payload = booth_request(EIGHT, staffed(qualifier=True), ROOM, Mock(user_id_for=lambda n: 'u-' + n), QUIET)
        self.assertEqual(payload, {
            'requestKey': 'ttp:2026-11-06T01:00:00.000Z', 'twitchChannel': 'Z1Rracing5',
            'scheduledAt': '2026-11-06T01:00:00+00:00', 'title': 'TTP Nightly · Torneo Corto qualifier',
            'racers': [], 'commentatorUserIds': ['u-Alice'], 'trackerUserId': 'u-Bob', 'tracker2UserId': None,
            'raceSlug': 'z1r/brave-goriya-1234',
        })

    def test_no_booked_channel_means_no_booth(self):
        self.assertIsNone(booth_request(EIGHT, {'crew': {'comms1': {'name': 'Alice'}}, 'bookings': []}, ROOM, None, QUIET))

    def test_before_the_room_exists_the_booth_opens_without_one(self):
        self.assertNotIn('raceSlug', booth_request(EIGHT, staffed(), None, Mock(user_id_for=lambda n: 'u'), QUIET))


class NightlyBoothsTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_the_coming_week_once_a_day_with_one_request_id_per_race(self):
        requests = _Requests({'/nightlies': _Response(200, {})})
        nightly = booths(requests)
        now = datetime(2026, 11, 2, 12, 0, tzinfo=TIMEZONE)  # a Monday
        count = await nightly.register_upcoming(now)
        self.assertGreater(count, 10)
        bodies = [call['json'] for call in requests.calls]
        self.assertTrue(all(b['editionId'] == 'ttp-nightly:2026' and b['requestId'] == 'nightly-register:' + b['startsAt'] for b in bodies))
        self.assertEqual(len({b['startsAt'] for b in bodies}), len(bodies), 'each occurrence once')
        self.assertEqual(await nightly.register_upcoming(now + timedelta(hours=23)), 0, 'not again within the day')
        self.assertGreater(await nightly.register_upcoming(now + timedelta(days=1)), 0, 'again the next day')

    async def test_no_booth_before_the_room_exists(self):
        requests = _Requests({'/games/': _Response(200, staffed()),
                              '/internal/relay/ttp/broadcast': _Response(200, {'outcome': 'staged', 'broadcastId': 'b1'})})
        self.assertIsNone(await booths(requests).maybe_open_booth(EIGHT, None, EIGHT - timedelta(minutes=20)))
        self.assertFalse(any('ttp/broadcast' in c['url'] for c in requests.calls))

    async def test_nightly_heads_up_at_thirty_five_then_booking_at_thirty(self):
        requests = _Requests({'/games/': _Response(200, staffed()),
                              '/internal/relay/booking-features': _Response(200, {'prepareOnly': True, 'slate': True}),
                              '/internal/relay/ttp/broadcast': _Response(200, {'outcome': 'not_due'})})
        nightly = booths(requests)
        self.assertEqual((await nightly.maybe_prepare_booth(EIGHT, EIGHT - timedelta(minutes=35))).outcome, 'not_due')
        self.assertIsNone(await nightly.maybe_prepare_booth(EIGHT, EIGHT - timedelta(minutes=34)))
        await nightly.maybe_open_booth(EIGHT, ROOM, EIGHT - timedelta(minutes=30))
        posts = [c['json'] for c in requests.calls if 'ttp/broadcast' in c['url']]
        self.assertEqual(len(posts), 2)
        self.assertIs(posts[0]['prepareOnly'], True)
        self.assertNotIn('raceSlug', posts[0])
        # The real booking still goes, with its room, whatever the heads-up answered.
        self.assertNotIn('prepareOnly', posts[1])
        self.assertEqual(posts[1]['raceSlug'], 'z1r/brave-goriya-1234')

    async def test_nightly_heads_up_only_in_its_window(self):
        requests = _Requests({'/games/': _Response(200, staffed()),
                              '/internal/relay/booking-features': _Response(200, {'prepareOnly': True})})
        nightly = booths(requests)
        self.assertIsNone(await nightly.maybe_prepare_booth(EIGHT, EIGHT - timedelta(minutes=36)))
        self.assertIsNone(await nightly.maybe_prepare_booth(EIGHT, EIGHT - timedelta(minutes=30)))
        self.assertEqual(requests.calls, [])

    def test_races_in_heads_up_window(self):
        from ttpbot.nightly import races_in_heads_up_window
        self.assertIn(EIGHT, races_in_heads_up_window(EIGHT - timedelta(minutes=33)))
        self.assertNotIn(EIGHT, races_in_heads_up_window(EIGHT - timedelta(minutes=30)))

    async def test_the_same_request_is_not_resent(self):
        requests = _Requests({'/games/': _Response(200, staffed()),
                              '/internal/relay/ttp/broadcast': _Response(200, {'outcome': 'staged', 'broadcastId': 'b1'})})
        nightly = booths(requests)
        await nightly.maybe_open_booth(EIGHT, ROOM, EIGHT - timedelta(minutes=30))
        await nightly.maybe_open_booth(EIGHT, ROOM, EIGHT - timedelta(minutes=29))
        self.assertEqual(len([c for c in requests.calls if 'ttp/broadcast' in c['url']]), 1)

    async def test_the_same_request_is_not_resent_when_only_the_slate_changes(self):
        requests = _Requests({'/games/': _Response(200, staffed()),
                              '/internal/relay/ttp/broadcast': _Response(200, {'outcome': 'staged', 'broadcastId': 'b1'})})
        slate = ChannelSlate()
        nightly = booths(requests)
        nightly.slate = slate
        await nightly.maybe_open_booth(EIGHT, ROOM, EIGHT - timedelta(minutes=30))
        slate.publish('league', [SlateEntry('Z1Rracing5', TEN, 2)])
        await nightly.maybe_open_booth(EIGHT, ROOM, EIGHT - timedelta(minutes=29))
        sent = [c for c in requests.calls if 'ttp/broadcast' in c['url']]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['json']['slate'], [])

    async def test_publish_slate_counts_each_staffed_nightly_as_four(self):
        # 7 PM: the 8 PM and 10 PM races are staffed on Z1Rracing5; midnight has a channel but no crew.
        midnight = TEN + timedelta(hours=2)
        requests = _Requests({
            occurrence_match_id(EIGHT): _Response(200, staffed()),
            occurrence_match_id(TEN): _Response(200, staffed()),
            occurrence_match_id(midnight): _Response(200, {'crew': {}, 'bookings': [{'channel': 'Z1Rracing5'}]}),
        })
        slate = ChannelSlate()
        nightly = booths(requests)
        nightly.slate = slate
        now = EIGHT - timedelta(hours=1)
        await nightly.publish_slate(now)
        utc = lambda moment: moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        self.assertEqual(slate.for_booking('Z1Rracing5', now),
                         [{'scheduledAt': utc(EIGHT), 'racers': 4}, {'scheduledAt': utc(TEN), 'racers': 4}])
        reads = len(requests.calls)
        self.assertEqual(reads, 3)
        # Each occurrence is read at most once per 10 minutes.
        await nightly.publish_slate(now + timedelta(minutes=9))
        self.assertEqual(len(requests.calls), reads)
        await nightly.publish_slate(now + timedelta(minutes=10))
        self.assertEqual(len(requests.calls), 2 * reads)

    async def test_a_late_booking_still_opens_the_booth_after_the_start(self):
        # Council books the channel after the reminder; the race is already running.
        game = 'ttp-nightly:2026:N20261106T010000Z:1'
        requests = _Requests({'/games/' + game: _Response(200, staffed()),
                              '/internal/relay/ttp/broadcast': _Response(200, {'outcome': 'staged', 'broadcastId': 'b1'})})
        nightly = booths(requests)
        outcome = await nightly.maybe_open_booth(EIGHT, ROOM, EIGHT + timedelta(minutes=45))
        self.assertEqual(outcome.outcome, 'staged')
        self.assertEqual([c['json']['raceSlug'] for c in requests.calls if 'ttp/broadcast' in c['url']], ['z1r/brave-goriya-1234'])
        # Three hours after the start the race is over; no booth.
        self.assertIsNone(await booths(requests).maybe_open_booth(EIGHT, ROOM, EIGHT + timedelta(hours=3)))

    def test_the_booth_window_runs_from_the_room_to_three_hours_after_the_start(self):
        from ttpbot.nightly import booth_window_open, races_in_booth_window
        self.assertFalse(booth_window_open(EIGHT, EIGHT - timedelta(minutes=31)))
        self.assertTrue(booth_window_open(EIGHT, EIGHT - timedelta(minutes=30)))
        self.assertTrue(booth_window_open(EIGHT, EIGHT + timedelta(hours=2, minutes=59)))
        self.assertFalse(booth_window_open(EIGHT, EIGHT + timedelta(hours=3)))
        # Just after midnight the 10 PM race (yesterday's date) is still in its window.
        self.assertIn(TEN, races_in_booth_window(TEN + timedelta(hours=2, minutes=30)))

    async def test_unfeatured_nightly_gets_no_booth(self):
        requests = _Requests({'/games/': _Response(200, {'crew': {'comms1': {'name': 'Alice'}}, 'bookings': []})})
        self.assertIsNone(await booths(requests).maybe_open_booth(EIGHT, ROOM, EIGHT - timedelta(minutes=30)))
        self.assertFalse(any('ttp/broadcast' in c['url'] for c in requests.calls))

    async def test_an_unstaffed_nightly_gets_no_booth(self):
        requests = _Requests({'/games/': _Response(200, {'crew': {}, 'bookings': []})})
        self.assertIsNone(await booths(requests).maybe_open_booth(TEN, ROOM, TEN - timedelta(minutes=20)))
        self.assertFalse(any('ttp/broadcast' in c['url'] for c in requests.calls))


class SchedulerBoothTests(unittest.IsolatedAsyncioTestCase):
    """The scheduler opens a featured nightly's booth in the tick that creates its room (Bogie, 2026-10-29)."""

    def bot(self, booth_status=200):
        self.requests = _Requests({'/games/ttp-nightly:2026:N20261106T010000Z:1': _Response(200, staffed()),
                                   '/internal/relay/ttp/broadcast': _Response(booth_status, {'outcome': 'staged', 'broadcastId': 'b1'})})
        nightly = booths(self.requests)
        nightly.register_upcoming = _done
        rooms = []

        async def create(race_time):
            rooms.append(race_time)
            return ROOM
        fake = SimpleNamespace(created_races={}, sent_webhooks=set(), _save_created_races=lambda: None,
                               _save_sent_webhooks=lambda: None, _create_race_room=create, nightly=nightly,
                               loop=SimpleNamespace(create_task=lambda coro: coro.close()), _send_webhook=_done,
                               logger=QUIET)
        fake.rooms = rooms
        return fake

    def booth_calls(self):
        return [c for c in self.requests.calls if 'ttp/broadcast' in c['url']]

    async def test_booth_opens_with_its_room(self):
        bot = self.bot()
        await _tick(bot, EIGHT - timedelta(minutes=30))
        self.assertEqual(bot.rooms, [EIGHT])
        self.assertEqual([c['json']['raceSlug'] for c in self.booth_calls()], ['z1r/brave-goriya-1234'])

    async def test_the_tick_publishes_the_slate_before_any_booth_asks(self):
        bot = self.bot()
        order = []
        publish, open_booth = bot.nightly.publish_slate, bot.nightly.maybe_open_booth

        async def published(now):
            order.append('slate')
            return await publish(now)

        async def opened(*args):
            order.append('booth')
            return await open_booth(*args)
        bot.nightly.publish_slate, bot.nightly.maybe_open_booth = published, opened
        await _tick(bot, EIGHT - timedelta(minutes=30))
        self.assertEqual(order[0], 'slate')
        self.assertIn('booth', order)

    async def test_a_failing_slate_never_costs_a_nightly_its_booth(self):
        bot = self.bot()

        async def broken(now):
            raise ValueError('slate')
        bot.nightly.publish_slate = broken
        await _tick(bot, EIGHT - timedelta(minutes=30))
        self.assertEqual(len(self.booth_calls()), 1)

    def test_the_builder_shares_the_bots_slate(self):
        from ttpbot.bot import TTPBot
        slate = ChannelSlate()
        bot = SimpleNamespace(logger=QUIET, slate=slate)
        env = {'TTPBOT_NIGHTLY_BOOTHS_ENABLED': '1', 'COMPETITIONS_URL': 'https://data.example',
               'COMPETITIONS_ENVIRONMENT': 'staging', 'TTPBOT_NIGHTLY_EDITION': '2026',
               'TTPBOT_NIGHTLY_ACTOR': '123456789012345678', 'COMPETITIONS_READER_TOKEN': 'read'}
        with patch.dict('os.environ', env, clear=False):
            nightly = TTPBot._build_nightly_booths(bot, Mock())
        self.assertIs(nightly.slate, slate)

    async def test_late_retry_keeps_its_room(self):
        bot = self.bot(booth_status=500)
        await _tick(bot, EIGHT - timedelta(minutes=30))
        self.requests.answers['/internal/relay/ttp/broadcast'] = _Response(200, {'outcome': 'staged', 'broadcastId': 'b1'})
        await _tick(bot, EIGHT + timedelta(hours=2, minutes=30))
        self.assertEqual([c['json'].get('raceSlug') for c in self.booth_calls()], ['z1r/brave-goriya-1234'] * 2)


async def _done(*_):
    return 0


async def _tick(fake, at):
    from ttpbot.bot import TTPBot
    clock = Mock(wraps=datetime)
    clock.now = lambda tz=None: at
    clock.fromisoformat = datetime.fromisoformat
    with patch('ttpbot.bot.datetime', clock):
        await TTPBot._check_and_create_races(fake)
