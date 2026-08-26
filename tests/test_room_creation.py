from datetime import datetime
import unittest
from unittest.mock import Mock, patch

import aiohttp

from ttpbot.bot import TTPBot, race_room_form_data
from ttpbot.config import TIMEZONE
from ttpbot.provider import RacetimeProvider


class FakeResponse:
    def __init__(self, status, *, location=None, body=""):
        self.status = status
        self.headers = {} if location is None else {"Location": location}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return self._body


class RequestRecorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def bot_for(origin, *, webhook=None, role=None):
    bot = object.__new__(TTPBot)
    bot.provider = RacetimeProvider(origin, "z1rr")
    bot.category_slug = "z1rr"
    bot.access_token = "oauth-token"
    bot.discord_webhook_url = webhook
    bot.race_seekers_role_id = role
    bot.logger = Mock()
    return bot


class RoomCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_outcomes_create_at_provider_and_resolve_location(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        for origin in ("https://racetime.gg", "https://raceroom.z1rracing.com"):
            with self.subTest(origin=origin):
                recorder = RequestRecorder([FakeResponse(201, location="/z1rr/example-room")])
                with patch("ttpbot.bot.aiohttp.request", recorder):
                    result = await bot_for(origin)._create_race_room(scheduled)
                self.assertEqual(result, origin + "/z1rr/example-room")
                self.assertEqual(len(recorder.calls), 1)
                call = recorder.calls[0]
                self.assertEqual(call["method"], "post")
                self.assertEqual(call["url"], origin + "/o/z1rr/startrace")
                self.assertEqual(call["headers"]["Authorization"], "Bearer oauth-token")
                self.assertEqual(call["data"], race_room_form_data(scheduled))
                self.assertIsInstance(call["timeout"], aiohttp.ClientTimeout)
                self.assertEqual(call["timeout"].total, 15)

    async def test_wrong_or_missing_location_fails_without_rewriting(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        for location in (None, "https://evil.example/z1rr/room", "/z1r/wrong"):
            recorder = RequestRecorder([FakeResponse(201, location=location)])
            bot = bot_for("https://raceroom.z1rracing.com")
            with self.subTest(location=location), patch("ttpbot.bot.aiohttp.request", recorder):
                self.assertIsNone(await bot._create_race_room(scheduled))
            rendered = repr(bot.logger.method_calls)
            self.assertNotIn("evil.example", rendered)

    async def test_provider_errors_never_log_response_body_or_token(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        recorder = RequestRecorder([FakeResponse(500, body="secret upstream body")])
        bot = bot_for("https://racetime.gg")
        with patch("ttpbot.bot.aiohttp.request", recorder):
            self.assertIsNone(await bot._create_race_room(scheduled))
        rendered = repr(bot.logger.method_calls)
        self.assertNotIn("secret upstream body", rendered)
        self.assertNotIn("oauth-token", rendered)


class AnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def test_announcement_uses_configured_webhook_role_and_provider_url(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        webhook = "https://discord.com/api/webhooks/12345/redacted-test-token"
        recorder = RequestRecorder([FakeResponse(204)])
        bot = bot_for(
            "https://raceroom.z1rracing.com",
            webhook=webhook,
            role="1494076623442542735",
        )
        with patch("ttpbot.bot.aiohttp.request", recorder):
            result = await bot._send_webhook(
                scheduled,
                "https://raceroom.z1rracing.com/z1rr/example-room",
            )
        self.assertTrue(result)
        call = recorder.calls[0]
        self.assertEqual(call["url"], webhook)
        self.assertEqual(call["method"], "post")
        self.assertEqual(
            call["json"]["allowed_mentions"],
            {"parse": [], "roles": ["1494076623442542735"]},
        )
        self.assertIn(
            "<@&1494076623442542735>", call["json"]["content"]
        )
        self.assertIn(
            "https://raceroom.z1rracing.com/z1rr/example-room",
            call["json"]["content"],
        )

    async def test_disabled_announcements_make_no_request(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        recorder = RequestRecorder([])
        bot = bot_for("https://racetime.gg")
        with patch("ttpbot.bot.aiohttp.request", recorder):
            result = await bot._send_webhook(
                scheduled, "https://racetime.gg/z1rr/example-room"
            )
        self.assertFalse(result)
        self.assertEqual(recorder.calls, [])

    async def test_announcement_rejects_wrong_provider_room_url(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        recorder = RequestRecorder([])
        bot = bot_for(
            "https://raceroom.z1rracing.com",
            webhook="https://discord.com/api/webhooks/12345/test-token",
            role="12345",
        )
        with patch("ttpbot.bot.aiohttp.request", recorder):
            result = await bot._send_webhook(
                scheduled, "https://racetime.gg/z1rr/example-room"
            )
        self.assertFalse(result)
        self.assertEqual(recorder.calls, [])


if __name__ == "__main__":
    unittest.main()
