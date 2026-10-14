import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ttpbot.chat_context import ChatContextPublisher


class ChatContextTests(unittest.IsolatedAsyncioTestCase):
    def publisher(self):
        return ChatContextPublisher('https://bot.example.test/z1rrbot', 'synthetic-token-' * 3, 'staging', logging.getLogger('test'))

    async def test_exact_room_single_and_coop_with_dedicated_credential(self):
        publisher = self.publisher()
        with patch('ttpbot.chat_context.aiohttp.request') as request:
            request.return_value.__aenter__ = AsyncMock(return_value=Mock(status=200))
            request.return_value.__aexit__ = AsyncMock(return_value=False)
            for coop in (False, True):
                self.assertTrue(await publisher.publish('https://racetime.gg/z1r/room-{}'.format(str(coop).lower()), 'fixture-1', coop))
            args = request.call_args.kwargs
            self.assertEqual(args['method'], 'PUT')
            self.assertTrue(args['url'].endswith('/v1/races/racetime-gg/z1r%2Froom-true/format'))
            self.assertEqual(args['json'], {'format': 'coop', 'sourceId': 'league:fixture-1'})
            self.assertEqual(args['headers']['X-Z1RR-Environment'], 'staging')
            self.assertEqual(args['headers']['Authorization'], 'Bearer ' + publisher.token)
            self.assertEqual(args['timeout'].total, 5)
            self.assertFalse(args['allow_redirects'])

    async def test_failure_is_retried_and_only_success_is_cached(self):
        publisher = self.publisher()
        with patch('ttpbot.chat_context.aiohttp.request') as request:
            request.return_value.__aenter__ = AsyncMock(side_effect=[Mock(status=503), Mock(status=200)])
            request.return_value.__aexit__ = AsyncMock(return_value=False)
            args = ('https://racetime.gg/z1r/room', 'fixture', False)
            self.assertFalse(await publisher.publish(*args))
            self.assertTrue(await publisher.publish(*args))
            self.assertTrue(await publisher.publish(*args))
            self.assertEqual(request.call_count, 2)

    async def test_foreign_room_and_uncertain_marker_never_send(self):
        with patch('ttpbot.chat_context.aiohttp.request') as request:
            for room in ('uncertain', 'https://evil.test/z1r/room', 'https://raceroom.z1rracing.com/z1rr/room'):
                self.assertFalse(await self.publisher().publish(room, 'fixture', False))
            request.assert_not_called()

    async def test_exception_logs_no_token_or_raw_url(self):
        publisher = self.publisher()
        with patch('ttpbot.chat_context.aiohttp.request', side_effect=RuntimeError(publisher.token)), self.assertLogs('test', level='WARNING') as logs:
            self.assertFalse(await publisher.publish('https://racetime.gg/z1r/room', 'fixture', False))
        self.assertNotIn(publisher.token, str(logs.output))

    def test_missing_or_invalid_optional_config_does_not_disable_scheduling(self):
        logger = Mock()
        self.assertIsNone(ChatContextPublisher.from_env({}, logger))
        self.assertIsNone(ChatContextPublisher.from_env({'Z1RR_CHAT_CONTEXT_URL': 'https://example.test'}, logger))
        configured = ChatContextPublisher.from_env({'Z1RR_CHAT_CONTEXT_URL': 'https://example.test',
            'Z1RR_CHAT_CONTEXT_TOKEN': 't' * 32, 'Z1RR_CHAT_CONTEXT_ENVIRONMENT': 'staging'}, logger)
        self.assertIsNotNone(configured)
