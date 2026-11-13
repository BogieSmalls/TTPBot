"""The channel slate: each booth booking carries its channel's later races (Bogie, 2026-11-11)."""
import unittest
from datetime import datetime, timedelta, timezone

from ttpbot.booth_slate import ChannelSlate, SlateEntry, booking_signature
from ttpbot.config import TIMEZONE

# Tuesday Nov 10, 2026: 8 PM ET is 01:00Z on the 11th, 10 PM ET is 03:00Z.
EIGHT = datetime(2026, 11, 10, 20, 0, tzinfo=TIMEZONE)
TEN = EIGHT + timedelta(hours=2)


class ChannelSlateTests(unittest.TestCase):
    def test_for_booking_lists_later_races_on_the_channel(self):
        slate = ChannelSlate()
        slate.publish('nightly', [SlateEntry('Z1Rracing', TEN, 4)])
        slate.publish('league', [
            SlateEntry('Z1Rracing', EIGHT, 2),                          # the booked race itself
            SlateEntry('z1rracing', EIGHT + timedelta(hours=3), 2),     # another channel spelling
            SlateEntry('Z1Rracing2', TEN, 4),                           # another channel
            SlateEntry('Z1Rracing', EIGHT + timedelta(hours=12, minutes=1), 4),  # past the horizon
            SlateEntry('Z1Rracing', EIGHT - timedelta(hours=1), 4),     # earlier
        ])
        self.assertEqual(slate.for_booking('Z1RRACING', EIGHT), [
            {'scheduledAt': '2026-11-11T03:00:00.000Z', 'racers': 4},
            {'scheduledAt': '2026-11-11T04:00:00.000Z', 'racers': 2},
        ])
        self.assertEqual(slate.for_booking('Z1Rracing', EIGHT, horizon=timedelta(hours=1)), [])

    def test_publish_replaces_only_that_source(self):
        slate = ChannelSlate()
        slate.publish('league', [SlateEntry('Z1Rracing', TEN, 2)])
        slate.publish('nightly', [SlateEntry('Z1Rracing', TEN + timedelta(hours=1), 4)])
        slate.publish('league', [])
        self.assertEqual(slate.for_booking('Z1Rracing', EIGHT), [{'scheduledAt': '2026-11-11T04:00:00.000Z', 'racers': 4}])

    def test_never_more_than_24_entries(self):
        slate = ChannelSlate()
        slate.publish('league', [SlateEntry('Z1Rracing', EIGHT + timedelta(minutes=10 * i), 2) for i in range(1, 40)])
        self.assertEqual(len(slate.for_booking('Z1Rracing', EIGHT)), 24)

    def test_signature_ignores_slate_and_prepare_only(self):
        booking = {'requestKey': 'k', 'twitchChannel': 'Z1Rracing', 'racers': []}
        self.assertEqual(booking_signature(booking),
                         booking_signature({**booking, 'slate': [{'scheduledAt': 'x', 'racers': 4}], 'prepareOnly': True}))
        self.assertNotEqual(booking_signature(booking), booking_signature({**booking, 'twitchChannel': 'Z1Rracing2'}))

    def test_a_start_in_any_zone_is_sent_in_utc(self):
        slate = ChannelSlate()
        slate.publish('autumn:autumn', [SlateEntry('Z1Rracing', TEN.astimezone(timezone.utc), 2)])
        self.assertEqual(slate.for_booking('Z1Rracing', EIGHT), [{'scheduledAt': '2026-11-11T03:00:00.000Z', 'racers': 2}])


if __name__ == '__main__':
    unittest.main()
