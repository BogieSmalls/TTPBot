import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ttpbot.state import DestinationStateStore, StateStoreError

DESTINATION = 'https://racetime.gg|z1r'
KEY = '2026-09-03T20:00:00-04:00|sirlinkalot-vs-windfox470'
OTHER_KEY = '2026-09-03T20:00:00-04:00|seanfreston-vs-stags28'
ROOM = 'https://racetime.gg/z1r/clever-slug-1234'


class LeagueStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def store(self, kind='league_created_races', name='league_races.json'):
        return DestinationStateStore(name, DESTINATION, kind, data_dir=self.root)

    def test_round_trips_a_league_key(self):
        store = self.store()
        store.save({KEY: ROOM})
        self.assertEqual(store.load(), {KEY: ROOM})

    def test_two_same_slot_races_coexist(self):
        store = self.store()
        store.save({KEY: ROOM, OTHER_KEY: ROOM})
        self.assertEqual(len(store.load()), 2)

    def test_webhook_kind_accepts_league_keys(self):
        store = self.store('league_sent_webhooks', 'league_webhooks.json')
        store.save({KEY: True})
        self.assertEqual(store.load(), {KEY: True})

    def test_rejects_a_league_key_without_a_slug(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00-04:00': ROOM})

    def test_rejects_a_league_slug_with_illegal_characters(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00-04:00|bad slug': ROOM})

    def test_rejects_a_naive_timestamp(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00|a-vs-b': ROOM})

    def test_still_validates_room_urls_against_the_destination(self):
        with self.assertRaises(StateStoreError):
            self.store().save({KEY: 'https://example.com/z1r/room'})

    def test_cleanup_ages_out_league_keys_by_timestamp(self):
        store = self.store()
        old = '2020-01-01T20:00:00-05:00|a-vs-b'
        store.save({KEY: ROOM, old: ROOM})

        retained = store.cleanup_before(datetime.now(timezone.utc) - timedelta(hours=2))

        self.assertIn(KEY, retained)
        self.assertNotIn(old, retained)

    def test_ttp_store_rejects_a_league_key(self):
        ttp = self.store('created_races', 'created_races.json')
        with self.assertRaises(StateStoreError):
            ttp.save({KEY: ROOM})

    def test_league_store_rejects_a_bare_timestamp_ttp_key(self):
        with self.assertRaises(StateStoreError):
            self.store().save({'2026-09-03T20:00:00-04:00': ROOM})

    def test_ttp_store_still_round_trips_its_own_keys(self):
        ttp = self.store('created_races', 'created_races.json')
        ttp.save({'2026-09-03T20:00:00-04:00': ROOM})
        self.assertEqual(ttp.load(), {'2026-09-03T20:00:00-04:00': ROOM})


if __name__ == '__main__':
    unittest.main()
