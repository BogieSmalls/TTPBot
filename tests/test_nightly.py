"""TTP Nightly booths (Bogie, 2026-10-24): Council staffs a nightly race; TTPBot opens its booth."""
import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

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

    async def test_opens_a_staffed_booth_at_the_reminder_and_again_only_when_the_room_arrives(self):
        game = 'ttp-nightly:2026:N20261106T010000Z:1'
        requests = _Requests({'/games/' + game: _Response(200, staffed()),
                              '/internal/relay/ttp/broadcast': _Response(200, {'outcome': 'staged', 'broadcastId': 'b1'})})
        nightly = booths(requests)
        self.assertIsNone(await nightly.maybe_open_booth(EIGHT, None, EIGHT - timedelta(minutes=25)), 'not before the reminder')
        outcome = await nightly.maybe_open_booth(EIGHT, None, EIGHT - timedelta(minutes=20))
        self.assertEqual(outcome.outcome, 'staged')
        booth_calls = [c for c in requests.calls if 'ttp/broadcast' in c['url']]
        self.assertEqual(len(booth_calls), 1)
        self.assertNotIn('raceSlug', booth_calls[0]['json'])
        # The same request again is not resent; the room arriving is.
        await nightly.maybe_open_booth(EIGHT, None, EIGHT - timedelta(minutes=19))
        await nightly.maybe_open_booth(EIGHT, ROOM, EIGHT - timedelta(minutes=18))
        booth_calls = [c for c in requests.calls if 'ttp/broadcast' in c['url']]
        self.assertEqual(len(booth_calls), 2)
        self.assertEqual(booth_calls[1]['json']['raceSlug'], 'z1r/brave-goriya-1234')

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

    def test_the_booth_window_runs_from_the_reminder_to_three_hours_after_the_start(self):
        from ttpbot.nightly import booth_window_open, races_in_booth_window
        self.assertFalse(booth_window_open(EIGHT, EIGHT - timedelta(minutes=21)))
        self.assertTrue(booth_window_open(EIGHT, EIGHT - timedelta(minutes=20)))
        self.assertTrue(booth_window_open(EIGHT, EIGHT + timedelta(hours=2, minutes=59)))
        self.assertFalse(booth_window_open(EIGHT, EIGHT + timedelta(hours=3)))
        # Just after midnight the 10 PM race (yesterday's date) is still in its window.
        self.assertIn(TEN, races_in_booth_window(TEN + timedelta(hours=2, minutes=30)))

    async def test_an_unstaffed_nightly_gets_no_booth(self):
        requests = _Requests({'/games/': _Response(200, {'crew': {}, 'bookings': []})})
        self.assertIsNone(await booths(requests).maybe_open_booth(TEN, ROOM, TEN - timedelta(minutes=20)))
        self.assertFalse(any('ttp/broadcast' in c['url'] for c in requests.calls))
