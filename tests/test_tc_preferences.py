import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from tests.test_handler_commands import command_handler
from tests.test_nightly import _Requests, _Response

AT = datetime(2026, 11, 6, 2, tzinfo=timezone.utc)
ROOM = 'https://racetime.gg/z1r/qualifier-one'
MESSAGE = {'id':'m1', 'posted_at':AT.isoformat(), 'user':{'id':'rt1','name':'Alpha'}}


def snapshot():
    return {'games':[
        {'editionId':'ttp-nightly:2026', 'matchId':'N20261106T0{}0000Z'.format(hour), 'associations':['corto-qualifier']}
        for hour in [1,3,5]] + [
        {'editionId':'ttp-nightly:2026', 'matchId':'N20261113T010000Z', 'associations':['corto-qualifier']}]}


def client(requests, rooms=None):
    from ttpbot.tc_preferences import TcPreferences
    return TcPreferences(portal_url='http://127.0.0.1:3012', token='chat',
                         owner_url='https://data.example', reader_token='read', environment='production',
                         edition='2026', rooms=lambda: rooms if rooms is not None else {AT.replace(hour=1).isoformat():ROOM},
                         requester=requests, now=lambda:AT)


class TcCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_wires_configured_client_and_keeps_rooms_through_qualifiers(self):
        from ttpbot.bot import TTPBot
        from tests.test_nightly import _tick
        bot = object.__new__(TTPBot)
        bot.created_races = {AT.replace(hour=1).isoformat():ROOM}
        bot.logger = Mock()
        env = {'TTPBOT_PORTAL_URL':'http://127.0.0.1:3012','Z1RR_TC_CHAT_TOKEN':'chat',
               'COMPETITIONS_URL':'https://data.example','COMPETITIONS_READER_TOKEN':'read',
               'COMPETITIONS_ENVIRONMENT':'production','TTPBOT_NIGHTLY_EDITION':'2026'}
        with patch.dict('os.environ',env,clear=True):
            configured = bot._build_tc_preferences()
        self.assertIsNotNone(configured)
        self.assertIs(configured.rooms(),bot.created_races)
        bot.tc_preferences = configured
        bot.sent_webhooks = set()
        bot._save_created_races = Mock()
        bot._save_sent_webhooks = Mock()
        bot._create_race_room = AsyncMock(return_value=None)
        bot.nightly = None
        bot.loop = SimpleNamespace(create_task=lambda coro:coro.close())
        await _tick(bot,AT+timedelta(hours=3))
        self.assertIn(ROOM,bot.created_races.values())
        await _tick(bot,AT+timedelta(hours=6))
        self.assertNotIn(ROOM,bot.created_races.values())

    async def test_commands_use_speaker_identity_and_confirm_after_save(self):
        handler = command_handler()
        backend = AsyncMock()
        handler.tc_preferences = backend

        async def record(room, message, declined):
            self.assertEqual(handler.messages, [])
            self.assertEqual(message, MESSAGE)
            return {'edition':34, 'declined':declined}

        backend.record.side_effect = record
        await handler.ex_optout([], MESSAGE)
        self.assertIn('sitting out TC34', handler.messages[0])
        self.assertIn('still race the qualifiers', handler.messages[0])
        handler.messages.clear()
        await handler.ex_optin([], MESSAGE)
        self.assertIn('eligible for TC34', handler.messages[0])
        self.assertIn('does not claim a seat', handler.messages[0])

    async def test_cannot_name_someone_else_or_claim_success_on_failure(self):
        handler = command_handler()
        handler.tc_preferences = AsyncMock()
        await handler.ex_optout(['OtherRacer'], MESSAGE)
        handler.tc_preferences.record.assert_not_called()
        handler.messages.clear()
        handler.tc_preferences.record.side_effect = TimeoutError()
        await handler.ex_optout([], MESSAGE)
        self.assertIn('could not confirm', handler.messages[0])
        handler.messages.clear()
        handler.tc_preferences.record.side_effect = None
        handler.tc_preferences.record.return_value = {'error':'offers-started'}
        await handler.ex_optin([], MESSAGE)
        self.assertIn('https://my.z1rracing.com', handler.messages[0])

    async def test_saved_request_groups_all_three_tagged_qualifiers_across_midnight(self):
        requests = _Requests({'/snapshot':_Response(200,snapshot()), '/preference':_Response(200,{'edition':34,'declined':True})})
        result = await client(requests).record('z1r/qualifier-one', MESSAGE, True)
        self.assertEqual(result, {'edition':34,'declined':True})
        payload = requests.calls[-1]['json']
        self.assertEqual(payload, {'racetimeId':'rt1','name':'Alpha','declined':True,'messageId':'m1',
                                  'postedAt':AT.isoformat(),'nightKey':'2026-11-05',
                                  'firstRaceAt':'2026-11-06T01:00:00+00:00','lastRaceAt':'2026-11-06T05:00:00+00:00'})
        self.assertEqual(requests.calls[-1]['headers']['Authorization'],'Bearer chat')
        self.assertEqual(requests.calls[0]['headers']['Authorization'],'Bearer read')

    async def test_untagged_unknown_and_stale_rooms_never_write_portal(self):
        for rooms, tagged in [({},True), ({AT.isoformat():ROOM},True),
                              ({(AT-timedelta(days=7)).isoformat():ROOM},True),
                              ({AT.replace(hour=1).isoformat():ROOM},False)]:
            data = snapshot()
            if not tagged:
                data['games'][0]['associations'] = []
            requests = _Requests({'/snapshot':_Response(200,data)})
            result = await client(requests,rooms).record('z1r/qualifier-one',MESSAGE,True)
            self.assertEqual(result,{'error':'outside-qualifiers'})
            self.assertFalse(any('/preference' in c['url'] for c in requests.calls))

    async def test_owner_failure_never_writes_portal(self):
        requests = _Requests({'/snapshot':_Response(503,{})})
        with self.assertRaises(RuntimeError):
            await client(requests).record('z1r/qualifier-one',MESSAGE,True)
        self.assertEqual(len(requests.calls),1)
