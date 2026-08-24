import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.fakes.racetime_provider import FakeRacetimeProvider
from ttpbot.bot import TTPBot, UNCERTAIN_RACE, build_state_stores
from ttpbot.config import TIMEZONE
from ttpbot.provider import RacetimeProvider
from ttpbot.schedule import race_info_for_time
from ttpbot.state import StateStoreError



class TimeoutContext:
    async def __aenter__(self):
        raise asyncio.TimeoutError()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class TimeoutStartraceDispatcher:
    def __init__(self, regular_request):
        self.regular_request = regular_request
        self.post_calls = 0

    def __call__(self, **kwargs):
        if kwargs.get("method") == "post" and kwargs.get("url", "").endswith("/startrace"):
            self.post_calls += 1
            return TimeoutContext()
        return self.regular_request(**kwargs)
def make_bot(provider, data_dir, webhook_url, *, stores=None):
    bot = object.__new__(TTPBot)
    bot.provider = provider
    bot.category_slug = provider.category
    bot.access_token = "fixture-token"
    bot.discord_webhook_url = webhook_url
    bot.race_seekers_role_id = "12345"
    bot.logger = Mock()
    bot.loop = asyncio.get_running_loop()
    created, sent = stores or build_state_stores(provider, data_dir)
    bot.created_race_store = created
    bot.sent_webhook_store = sent
    bot.created_races = created.load()
    bot.sent_webhooks = set(sent.load())
    return bot, (created, sent)


class ProviderIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_temp)
        self.root = Path(self.temp.name)
        self.fake = await FakeRacetimeProvider().start()
        self.addAsyncCleanup(self.fake.close)
        self.provider = RacetimeProvider(
            self.fake.origin,
            "z1rr",
            allow_insecure_loopback=True,
        )

    async def _cleanup_temp(self):
        self.temp.cleanup()

    async def test_one_room_one_webhook_and_restart_has_no_duplicate(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        now = scheduled - timedelta(minutes=15)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz is not None else now.replace(tzinfo=None)

        bot, stores = make_bot(
            self.provider,
            self.root,
            self.fake.origin + "/discord-webhook",
        )
        with (
            patch("ttpbot.bot.datetime", FrozenDateTime),
            patch("ttpbot.bot.get_upcoming_races", return_value=[scheduled]),
        ):
            await bot._check_and_create_races()
            await asyncio.sleep(0.05)
        self.assertEqual(len(self.fake.room_posts), 1)
        self.assertEqual(len(self.fake.webhooks), 1)
        self.assertEqual(
            self.fake.room_posts[0]["authorization"], "Bearer fixture-token"
        )
        self.assertEqual(len(stores[0].load()), 1)
        self.assertEqual(len(stores[1].load()), 1)
        room_url = next(iter(stores[0].load().values()))
        self.assertEqual(room_url, self.fake.origin + "/z1rr/integration-room")

        restarted, _ = make_bot(
            self.provider,
            self.root,
            self.fake.origin + "/discord-webhook",
            stores=stores,
        )
        with (
            patch("ttpbot.bot.datetime", FrozenDateTime),
            patch("ttpbot.bot.get_upcoming_races", return_value=[scheduled]),
        ):
            await restarted._check_and_create_races()
            await asyncio.sleep(0.05)
        self.assertEqual(len(self.fake.room_posts), 1)
        self.assertEqual(len(self.fake.webhooks), 1)

    async def test_second_provider_cannot_reuse_first_provider_state(self):
        created, sent = build_state_stores(self.provider, self.root)
        created.save({
            "2026-08-24T20:00:00-04:00":
                self.fake.origin + "/z1rr/integration-room"
        })
        second = await FakeRacetimeProvider().start()
        self.addAsyncCleanup(second.close)
        other = RacetimeProvider(
            second.origin, "z1rr", allow_insecure_loopback=True
        )
        other_created, _ = build_state_stores(other, self.root)
        with self.assertRaises(StateStoreError):
            other_created.load()
        self.assertEqual(second.room_posts, [])

    async def test_wrong_location_and_provider_failure_never_create_state(self):
        for location, status in (
            ("https://evil.example/z1rr/room", 201),
            ("/z1rr/integration-room", 500),
        ):
            with self.subTest(location=location, status=status):
                case_root = self.root / "case-{}".format(status)
                case_root.mkdir(exist_ok=True)
                self.fake.location = location
                self.fake.startrace_status = status
                bot, stores = make_bot(
                    self.provider,
                    case_root,
                    self.fake.origin + "/discord-webhook",
                )
                result = await bot._create_race_room(
                    datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
                )
                self.assertIsNone(result)
                self.assertEqual(stores[0].load(), {})
                rendered = repr(bot.logger.method_calls)
                self.assertNotIn("evil.example", rendered)
                self.fake.startrace_status = 201

    async def test_state_write_failure_preserves_prior_file(self):
        bot, stores = make_bot(
            self.provider,
            self.root,
            self.fake.origin + "/discord-webhook",
        )
        stores[0].save({})
        before = stores[0].path.read_bytes()
        with patch.object(stores[0], "save", side_effect=StateStoreError("injected")):
            with self.assertRaises(StateStoreError):
                bot.created_races = {
                    "2026-08-24T20:00:00-04:00":
                        self.fake.origin + "/z1rr/integration-room"
                }
                bot._save_created_races()
        self.assertEqual(stores[0].path.read_bytes(), before)

    async def test_uncertain_timeout_recovers_exact_existing_room(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        self.fake.current_races = [{
            "name": "z1rr/integration-room",
            "goal": {"name": "Beat the game"},
            "info_bot": race_info_for_time(scheduled),
        }]
        bot, _ = make_bot(
            self.provider, self.root, self.fake.origin + "/discord-webhook"
        )
        import aiohttp
        dispatcher = TimeoutStartraceDispatcher(aiohttp.request)
        with patch("ttpbot.bot.aiohttp.request", dispatcher):
            result = await bot._create_race_room(scheduled)
        self.assertEqual(result, self.fake.origin + "/z1rr/integration-room")
        self.assertEqual(dispatcher.post_calls, 1)

    async def test_unresolved_timeout_persists_fail_closed_sentinel_across_restart(self):
        scheduled = datetime(2026, 8, 24, 20, 0, tzinfo=TIMEZONE)
        now = scheduled - timedelta(minutes=15)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz is not None else now.replace(tzinfo=None)

        bot, stores = make_bot(
            self.provider, self.root, self.fake.origin + "/discord-webhook"
        )
        import aiohttp
        dispatcher = TimeoutStartraceDispatcher(aiohttp.request)
        with (
            patch("ttpbot.bot.aiohttp.request", dispatcher),
            patch("ttpbot.bot.datetime", FrozenDateTime),
            patch("ttpbot.bot.get_upcoming_races", return_value=[scheduled]),
        ):
            await bot._check_and_create_races()
            await bot._check_and_create_races()
        self.assertEqual(dispatcher.post_calls, 1)
        self.assertEqual(
            stores[0].load(),
            {scheduled.isoformat(): UNCERTAIN_RACE},
        )
        self.assertEqual(self.fake.webhooks, [])

        restarted, _ = make_bot(
            self.provider,
            self.root,
            self.fake.origin + "/discord-webhook",
            stores=stores,
        )
        with (
            patch("ttpbot.bot.aiohttp.request", dispatcher),
            patch("ttpbot.bot.datetime", FrozenDateTime),
            patch("ttpbot.bot.get_upcoming_races", return_value=[scheduled]),
        ):
            await restarted._check_and_create_races()
        self.assertEqual(dispatcher.post_calls, 1)


if __name__ == "__main__":
    unittest.main()
