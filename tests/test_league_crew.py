import json
import logging
import tempfile
import unittest
from pathlib import Path

from ttpbot.league.crew import CrewDirectory

QUIET = logging.getLogger('test-league-crew')
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False

ROSTER = [
    {'id': 'u-k', 'name': 'SpecialK', 'discordId': '429', 'twitchLogin': 'specialk3782', 'role': 'operator'},
    {'id': 'u-g', 'name': 'GrandpaSzabo', 'discordId': '355', 'twitchLogin': 'grandpaszabo', 'role': 'admin'},
    {'id': 'u-s', 'name': 'Seanfreston', 'discordId': '120', 'twitchLogin': 'seanfreston', 'role': 'operator'},
]


class CrewDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cache = Path(self.dir.name) / 'crew.json'
        self.addCleanup(self.dir.cleanup)

    def directory(self, members=ROSTER):
        crew = CrewDirectory(cache_path=self.cache, logger=QUIET)
        crew.replace(members)
        return crew

    def test_resolves_a_dropdown_name_to_a_discord_id(self):
        crew = self.directory()

        self.assertEqual(crew.discord_id_for('SpecialK'), '429')
        # The sheet column is a dropdown of these exact names, but a stray
        # space or a different case must not cost someone their ping.
        self.assertEqual(crew.discord_id_for('  specialk  '), '429')

    def test_returns_none_rather_than_guessing(self):
        crew = self.directory()

        # "Sean" is not "Seanfreston". Near-misses are exactly how the wrong
        # person gets pinged before a live race, so there is no fuzzy path.
        self.assertIsNone(crew.discord_id_for('Sean'))
        self.assertIsNone(crew.discord_id_for(''))
        self.assertIsNone(crew.discord_id_for(None))

    def test_survives_a_control_plane_that_is_asleep(self):
        self.directory()

        # The control plane sleeps on idle, so the roster is regularly
        # unreachable at announce time. A cached answer beats no answer.
        reloaded = CrewDirectory(cache_path=self.cache, logger=QUIET)
        self.assertEqual(reloaded.discord_id_for('GrandpaSzabo'), '355')

    def test_starts_empty_when_there_is_no_cache_yet(self):
        crew = CrewDirectory(cache_path=self.cache, logger=QUIET)

        self.assertIsNone(crew.discord_id_for('SpecialK'))
        self.assertEqual(crew.size, 0)

    def test_ignores_malformed_members_without_losing_the_good_ones(self):
        crew = CrewDirectory(cache_path=self.cache, logger=QUIET)
        crew.replace([
            {'id': 'u-k', 'name': 'SpecialK', 'discordId': '429'},
            {'id': 'u-x', 'name': '', 'discordId': '1'},
            {'id': 'u-y', 'name': 'NoDiscord', 'discordId': ''},
            {'name': 'NoUserId', 'discordId': '999'},
            'not-a-dict',
        ])

        self.assertEqual(crew.size, 1)
        self.assertEqual(crew.discord_id_for('SpecialK'), '429')

    def test_a_corrupt_cache_is_not_fatal(self):
        self.cache.write_text('{ this is not json', encoding='utf-8')

        crew = CrewDirectory(cache_path=self.cache, logger=QUIET)

        self.assertEqual(crew.size, 0)

    def test_refuses_to_replace_a_good_roster_with_an_empty_one(self):
        crew = self.directory()

        crew.replace([])

        # An empty payload is far more likely to be a broken response than a
        # league that suddenly has no crew, and forgetting everyone silently
        # turns every mention into a plain name.
        self.assertEqual(crew.size, 3)


class MentionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.crew = CrewDirectory(
            cache_path=Path(self.dir.name) / 'crew.json', logger=QUIET,
        )
        self.crew.replace(ROSTER)

    def test_mentions_resolvable_crew_and_keeps_the_rest_as_text(self):
        mentions, ids = self.crew.mentions(('SpecialK', 'Sean'))

        # Unresolvable names still appear, because dropping a commentator from
        # the announcement is worse than not pinging them.
        self.assertEqual(mentions, ['<@429>', 'Sean'])
        self.assertEqual(ids, ['429'])


if __name__ == '__main__':
    unittest.main()


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.crew = CrewDirectory(
            cache_path=Path(self.dir.name) / 'crew.json', logger=QUIET,
        )

    def requester(self, response):
        def _request(**kwargs):
            self.seen = kwargs
            return response
        return _request

    async def test_adopts_a_fetched_roster_and_sends_the_token(self):
        ok = await self.crew.refresh(
            'https://cp/internal/relay/league/roster', 'tok',
            requester=self.requester(_FakeResponse(200, {'roster': ROSTER})),
        )

        self.assertTrue(ok)
        self.assertEqual(self.crew.discord_id_for('SpecialK'), '429')
        self.assertEqual(self.seen['headers']['Authorization'], 'Bearer tok')

    async def test_a_sleeping_control_plane_keeps_the_cached_roster(self):
        self.crew.replace(ROSTER)

        async def _boom(**_):
            raise OSError('connection refused')

        ok = await self.crew.refresh('https://cp/x', 'tok', requester=_boom)

        # The CP sleeps on idle. An unreachable roster must cost us freshness,
        # never the announcement.
        self.assertFalse(ok)
        self.assertEqual(self.crew.discord_id_for('SpecialK'), '429')

    async def test_a_rejected_token_keeps_the_cached_roster(self):
        self.crew.replace(ROSTER)

        ok = await self.crew.refresh(
            'https://cp/x', 'bad',
            requester=self.requester(_FakeResponse(401, {'error': 'unauthorized'})),
        )

        self.assertFalse(ok)
        self.assertEqual(self.crew.size, 3)

    async def test_does_nothing_without_configuration(self):
        ok = await self.crew.refresh('', '', requester=self.requester(None))

        self.assertFalse(ok)


class ManagedUserIdTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.crew = CrewDirectory(cache_path=Path(self.dir.name) / 'c.json', logger=QUIET)
        self.crew.replace(ROSTER)

    def test_resolves_the_managed_user_id_a_draft_stores(self):
        # A broadcast draft records crew as managed-user ids, not Discord ids.
        # Passing one where the other is expected invites nobody, silently.
        self.assertEqual(self.crew.user_id_for('SpecialK'), 'u-k')
        self.assertEqual(self.crew.discord_id_for('SpecialK'), '429')

    def test_returns_none_for_an_unknown_name(self):
        self.assertIsNone(self.crew.user_id_for('Sean'))
        self.assertIsNone(self.crew.user_id_for(''))

    def test_ignores_a_cache_written_before_ids_were_stored(self):
        cache = Path(self.dir.name) / 'legacy.json'
        cache.write_text('{"specialk": "429"}', encoding='utf-8')

        crew = CrewDirectory(cache_path=cache, logger=QUIET)

        # Half-loading it would look resolvable right up to the failure.
        self.assertEqual(crew.size, 0)
