import unittest
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from ttpbot.league.database_source import LeagueDatabaseSource
from ttpbot.league.roster import load_roster

class LeagueDatabaseSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_has_both_tonight_races_and_stale_copy_cannot_open_rooms(self):
        now=datetime(2026,10,16,22,20,tzinfo=timezone.utc)
        source=LeagueDatabaseSource('http://db/league','test','test',load_roster(),logging.getLogger('test'))
        book={'environment':'test','sourceId':'1MEyO03Wib6iyH7-75e-orh2K75AATwEoJB9HlTe9VgM','fetchedAt':now.isoformat(),'tables':[
            {'id':2033319762,'rows':[['Date','Time','Game','Runner 1','Runner 2'],['10/16/2026','7:00:00 PM','2','(DAMB) Lumaga','(TUG) BlessedBe'],['10/16/2026','10:00:00 PM','2','(Tek) ThePirateSheep','(TML) Merks']]},
            {'id':264392304,'rows':[['Week 7 - TTP3 Courage'],['1','Three Unique Gamers','Dodongo Ate My Baby'],['2','The missing links','TekTITANS']]},
        ]}
        source._read=AsyncMock(return_value=book)
        races=await source.races(now)
        self.assertEqual(len(races),2)
        self.assertEqual([r.game for r in races],[2,2])
        self.assertTrue(all(r.fixture and r.fixture.week==7 for r in races))
        self.assertEqual(races[0].start.hour,19)
        self.assertEqual(races[1].start.hour,22)
        self.assertEqual(await source.races(now+timedelta(minutes=2)),[])
        source._read=AsyncMock(side_effect=OSError('offline'))
        self.assertEqual(await source.races(now+timedelta(minutes=3)),[])
        self.assertEqual(len(source._snapshot['tables'][0]['rows']),3)

    async def test_wrong_environment_and_missing_tabs_are_not_a_fresh_schedule(self):
        source=LeagueDatabaseSource('http://db/league','test','production',load_roster(),logging.getLogger('test'))
        source._read=AsyncMock(return_value={'environment':'staging','fetchedAt':datetime.now(timezone.utc).isoformat(),'tables':[]})
        self.assertEqual(await source.races(datetime.now(timezone.utc)),[])
