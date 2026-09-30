"""The production bot and handler must receive what the Autumn runner prepares."""
import asyncio
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ttpbot.autumn.wiring import build_autumn_runner
from ttpbot.bot import TTPBot
from ttpbot.handler import TTPRaceHandler
from tests.test_autumn_adapters import Endpoint, Response, provider, race, Log
from ttpbot.autumn.rooms import create_autumn_room
from ttpbot.state import UNCERTAIN_RACE

NAME = 'z1r/fancy-mario-1234'
DATA = {'name': NAME, 'goal': {'name': 'Beat the game'},
        'info_user': 'Z1R Autumn — ISUMatt vs chessjerk [W1-1]',
        'status': {'value': 'open'}, 'entrants': []}

class LiveWiring(unittest.TestCase):
    def test_real_bot_accepts_autumn_but_still_ignores_finished_rooms(self):
        bot = object.__new__(TTPBot)
        self.assertTrue(bot.should_handle(DATA))
        self.assertFalse(bot.should_handle(dict(DATA, status={'value': 'finished'})))

    def test_invites_reach_a_handler_already_connected_before_the_tick(self):
        asyncio.run(self.check_invites(handler_first=True))

    def test_invites_reach_a_handler_connecting_after_the_tick(self):
        asyncio.run(self.check_invites(handler_first=False))

    async def check_invites(self, handler_first):
        log = logging.getLogger('autumn-test')
        bot = SimpleNamespace(state={}, autumn_racetime_ids={'ISUMatt': 'aaa', 'chessjerk': 'bbb'})
        runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x'}, bot, log)
        state = bot.state.setdefault(NAME, {})
        handler = TTPRaceHandler(conn=None, logger=log, state=state)
        handler.data = dict(DATA)
        handler.ws = SimpleNamespace(send=AsyncMock())
        handler.invite_user = AsyncMock()
        if handler_first:
            await handler.begin()
        await runner.scheduler._invite(race(), 'https://racetime.gg/' + NAME)
        if not handler_first:
            await handler.begin()
        await runner.scheduler._invite(race(), 'https://racetime.gg/' + NAME)
        self.assertEqual([c.args[0] for c in handler.invite_user.await_args_list], ['aaa', 'bbb'])
        self.assertEqual(list(bot.state), [NAME])
        await handler.end()
        self.assertNotIn('_autumn_send_invites', state)

    def test_announcement_reads_the_deployment_environment(self):
        async def check():
            runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x',
                'TTPBOT_AUTUMN_DISCORD_WEBHOOK_URL': 'https://discord.example/autumn'},
                SimpleNamespace(state={}), logging.getLogger('autumn-test'))
            runner.engine.state = AsyncMock(return_value={'document': {'racers': {}}})
            with patch('ttpbot.autumn.wiring.send_autumn_announcement', new_callable=AsyncMock) as send:
                send.return_value = True
                await runner.scheduler._announce(race(), None, 'https://racetime.gg/' + NAME)
                self.assertEqual(send.await_args.args[2], 'https://discord.example/autumn')
        asyncio.run(check())

    def test_created_room_without_location_stays_uncertain(self):
        endpoint = Endpoint(Response(201), Response(200, json={'current_races': []}))
        result = asyncio.run(create_autumn_room(race(), provider(), 'token', Log(), requester=endpoint))
        self.assertEqual(result, UNCERTAIN_RACE)
        self.assertEqual([call['method'] for call in endpoint.calls], ['post', 'get'])

    def test_announces_to_configured_channel_with_existing_bot_credentials(self):
        async def check():
            runner = build_autumn_runner({'Z1RR_ENGINE_TOKEN': 'x',
                'TTPBOT_AUTUMN_DISCORD_CHANNEL_ID': '1554172126880207009',
                'TTPBOT_LEAGUE_DISCORD_BOT_TOKEN': 'bot-secret'},
                SimpleNamespace(state={}), logging.getLogger('autumn-test'))
            runner.engine.state = AsyncMock(return_value={'document': {'racers': {}}})
            endpoint = Endpoint(Response(200, json={'id': '123'}))
            with patch('ttpbot.autumn.announce.aiohttp.request', endpoint):
                await runner.scheduler._announce(race(), None, 'https://racetime.gg/' + NAME)
            call, = endpoint.calls
            self.assertEqual(call['url'], 'https://discord.com/api/v10/channels/1554172126880207009/messages')
            self.assertEqual(call['headers']['Authorization'], 'Bot bot-secret')
            self.assertEqual(call['json']['allowed_mentions']['parse'], [])
        asyncio.run(check())

    def test_bot_credentials_are_not_attached_to_a_webhook(self):
        from ttpbot.autumn.announce import send_autumn_announcement
        endpoint = Endpoint(Response(204))
        sent = asyncio.run(send_autumn_announcement(race(), 'https://racetime.gg/' + NAME,
            'https://discord.example/webhook', Log(), requester=endpoint,
            bot_token='bot-secret', channel_id='1554172126880207009'))
        self.assertTrue(sent)
        self.assertNotIn('Authorization', endpoint.calls[0]['headers'])
