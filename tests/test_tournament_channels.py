import unittest
from unittest.mock import AsyncMock
from ttpbot.tournament_channels import TournamentChannels

class DestinationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_saved_edition_is_used_and_outage_retains_it(self):
        fetch = AsyncMock(return_value={'entries': [{'event': 'ttp', 'edition': '5', 'races': '1569388342590836757'}]})
        reader = TournamentChannels({}, fetch=fetch)
        self.assertEqual(await reader.races('ttp', '5'), '1569388342590836757')
        self.assertIsNone(await reader.races('ttp', '6'))
        fetch.side_effect = RuntimeError('offline')
        self.assertEqual(await reader.races('ttp', '5'), '1569388342590836757')

    async def test_rehearsal_never_reads_production_destinations(self):
        fetch = AsyncMock()
        reader = TournamentChannels({'TTPBOT_REHEARSAL': 'true'}, fetch=fetch)
        self.assertIsNone(await reader.races('ttp', '5'))
        fetch.assert_not_awaited()

    async def test_unreadable_first_copy_pauses_announcement(self):
        reader = TournamentChannels({}, fetch=AsyncMock(side_effect=RuntimeError('offline')))
        with self.assertRaises(RuntimeError):
            await reader.races('ttp', '5')
