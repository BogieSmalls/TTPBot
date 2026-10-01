import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch
from tests.test_autumn_adapters import race, provider, Endpoint, Response, Log, ROOM_PATH, ROOM_URL
from ttpbot.autumn.rooms import room_title, recover_autumn_room
from ttpbot.room_policy import is_autumn_room

class RoomTitles(unittest.TestCase):
    def sample(self, game=1, best_of=1):
        r=race('W1-29','1ganondwarf','AceKingSpade')
        r.identity=replace(r.identity,edition='2026',game=game)
        r.best_of=best_of
        r.room_marker='Z1RR:sample-action'
        return r

    def test_single_game_title_is_the_requested_display(self):
        self.assertEqual(room_title(self.sample()), '2026 Autumn Tournament \u2014 1ganondwarf vs AceKingSpade [W1-29]')

    def test_best_of_keeps_each_game_distinct_including_game_one(self):
        titles=[room_title(self.sample(n,3)) for n in (1,2,3)]
        self.assertEqual(len(set(titles)),3)
        for n,title in enumerate(titles,1):
            self.assertTrue(title.endswith('Game {}'.format(n)))
            self.assertNotIn('sample-action',title)

    def test_editions_do_not_share_a_title(self):
        r=self.sample(); first=room_title(r)
        r.identity=replace(r.identity,edition='2027')
        self.assertNotEqual(first,room_title(r))

    def test_bot_recognizes_new_title_and_legacy_title_after_seed_roll(self):
        for title in [room_title(self.sample()),'Z1R Autumn \u2014 A vs B [W1-1]']:
            self.assertTrue(is_autumn_room({'goal':{'name':'Beat the game'},'info_user':title,'info_bot':'Seed: unrelated'}))

    def test_recovery_accepts_the_clean_title(self):
        r=self.sample(); endpoint=Endpoint(Response(200,json={'current_races':[{'info_user':room_title(r),'url':ROOM_PATH}]}))
        with patch('ttpbot.autumn.rooms.aiohttp.request',endpoint):
            self.assertEqual(asyncio.run(recover_autumn_room(r,provider(),'token',Log())),ROOM_URL)

    def test_recovery_still_finds_predeployment_marker_title(self):
        r=self.sample(); old='Z1R Autumn \u2014 1ganondwarf vs AceKingSpade [W1-29] Game 1 [Z1RR:sample-action]'
        endpoint=Endpoint(Response(200,json={'current_races':[{'info_user':old,'url':ROOM_PATH}]}))
        with patch('ttpbot.autumn.rooms.aiohttp.request',endpoint):
            self.assertEqual(asyncio.run(recover_autumn_room(r,provider(),'token',Log())),ROOM_URL)

    def test_two_matching_rooms_are_not_guessed_at(self):
        r=self.sample();title=room_title(r)
        endpoint=Endpoint(Response(200,json={'current_races':[{'info_user':title,'url':ROOM_PATH},{'info_user':title,'url':'/z1r/other-room'}]}))
        with patch('ttpbot.autumn.rooms.aiohttp.request',endpoint):
            self.assertIsNone(asyncio.run(recover_autumn_room(r,provider(),'token',Log())))
