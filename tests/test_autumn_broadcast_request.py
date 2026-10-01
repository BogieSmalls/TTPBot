import importlib
import importlib.util
import unittest
from dataclasses import replace
from types import SimpleNamespace
from tests.test_autumn_adapters import race, Log, ROOM_URL, Endpoint, Response
from ttpbot.autumn.schedule import ScheduleRow
from ttpbot.autumn.matching import RaceIdentity

class AutumnBroadcastRequestTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ttpbot.autumn.broadcast_request'))
        self.build = importlib.import_module('ttpbot.autumn.broadcast_request').build_broadcast_request
        self.race = race()
        self.row = ScheduleRow(self.race.at, '(46) ISUMatt', '(7) chessjerk', 'Bogie', '', 'Tracker', 'Z1Rracing')
        self.document = {'twitchChannels': {'ISUMatt': 'isumatt', 'chessjerk': 'chessjerk'},
                         'racetimeIds': {'ISUMatt': 'id-one', 'chessjerk': 'id-two'},
                         'ranks': {'ISUMatt': 46, 'chessjerk': 7}}
        self.crew = SimpleNamespace(user_id_for=lambda name: {'Bogie': 'managed-1', 'Tracker': 'managed-2'}.get(name))

    def build_request(self, race=None, row=None, document=None):
        return self.build(race or self.race, row or self.row, ROOM_URL,
                          self.document if document is None else document, self.crew, Log(), edition='2026', number=1)

    def test_uses_canonical_accounts_and_managed_crew_ids(self):
        body = self.build_request()
        self.assertEqual(body['requestKey'], 'tournament:autumn:2026:W1-1:game:1')
        self.assertEqual(body['raceSlug'], 'z1r/fancy-mario-1234')
        self.assertEqual(body['racers'][0], {'slot': 1, 'channel': 'isumatt', 'displayName': 'ISUMatt', 'racetimeId': 'id-one', 'tournamentSeed': '#46'})
        self.assertEqual(body['commentatorUserIds'], ['managed-1'])
        self.assertEqual(body['trackerUserId'], 'managed-2')
        self.assertIn('#1', body['title'])

    def test_reschedule_keeps_identity_but_a_reset_has_its_own(self):
        from datetime import timedelta
        self.assertEqual(self.build_request()['requestKey'], self.build_request(race=replace(self.race, at=self.race.at+timedelta(days=1)))['requestKey'])
        final = replace(self.race, match_id='GF-1', identity=RaceIdentity('autumn', 'GF-1'))
        reset = replace(self.race, match_id='GF-2', identity=RaceIdentity('autumn', 'GF-2'))
        self.assertNotEqual(self.build_request(race=final)['requestKey'], self.build_request(race=reset)['requestKey'])

    def test_unassigned_channel_or_unknown_stream_does_not_make_a_booth(self):
        self.assertIsNone(self.build_request(row=replace(self.row, channel='')))
        missing = dict(self.document, twitchChannels={'ISUMatt': 'isumatt'})
        self.assertIsNone(self.build_request(document=missing))
        missing = dict(self.document, racetimeIds={'ISUMatt': 'id-one'})
        self.assertIsNone(self.build_request(document=missing))

    def test_the_existing_booth_transport_accepts_the_tournament_route(self):
        import asyncio
        from ttpbot.league.booth import request_booth
        endpoint = Endpoint(Response(200, json={'outcome': 'staged', 'broadcastId': 'one'}))
        answer = asyncio.run(request_booth(self.build_request(), 'https://cp.example', 'token', Log(), requester=endpoint,
                                          endpoint='/internal/relay/tournament/broadcast', label='Autumn'))
        self.assertEqual(answer.broadcast_id, 'one')
        self.assertEqual(endpoint.calls[0]['url'], 'https://cp.example/internal/relay/tournament/broadcast')
