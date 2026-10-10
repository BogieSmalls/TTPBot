import asyncio
import unittest
from dataclasses import replace
from datetime import datetime

from ttpbot.config import TIMEZONE
from ttpbot.league.announce import build_announcement
from ttpbot.league.roster import Racer
from ttpbot.league.schedule import LeagueRace

ROOM = 'https://racetime.gg/z1r/clever-slug-1234'


def _racer(name, discord_id):
    return Racer(
        sheet_name=name, team='SC', team_full='Shadow Cartel',
        display_name=name, twitch_channel=name.lower(),
        racetime_id='rt-' + name.lower(), discord_id=discord_id,
    )


def _race(one, two):
    return LeagueRace(
        start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
        runner_one=one, runner_two=two, channel=None,
    )


class BuildAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.race = _race(_racer('SirLinkalot', '111'), _racer('Windfox470', '222'))
        self.body = build_announcement(self.race, ROOM)

    def test_mentions_both_racers(self):
        self.assertIn('<@111>', self.body['content'])
        self.assertIn('<@222>', self.body['content'])

    def test_includes_the_room_url(self):
        self.assertIn(ROOM, self.body['embeds'][0]['description'])

    def test_allow_lists_exactly_the_two_racers(self):
        self.assertEqual(self.body['allowed_mentions'],
                         {'parse': [], 'users': ['111', '222']})

    def test_parse_is_always_empty(self):
        # A stray @everyone in a spreadsheet cell must not ping the server.
        race = _race(_racer('@everyone', '111'), _racer('Windfox470', '222'))
        body = build_announcement(race, ROOM)
        self.assertEqual(body['allowed_mentions']['parse'], [])

    def test_missing_discord_id_falls_back_to_the_display_name(self):
        race = _race(_racer('SirLinkalot', None), _racer('Windfox470', '222'))
        body = build_announcement(race, ROOM)

        self.assertIn('SirLinkalot', body['embeds'][0]['description'])
        self.assertNotIn('<@None>', body['content'])
        self.assertNotIn('<@>', body['content'])
        self.assertEqual(body['allowed_mentions']['users'], ['222'])

    def test_no_discord_ids_at_all_still_produces_a_post(self):
        race = _race(_racer('SirLinkalot', None), _racer('Windfox470', None))
        body = build_announcement(race, ROOM)

        self.assertEqual(body['allowed_mentions']['users'], [])
        self.assertIn(ROOM, body['embeds'][0]['description'])


if __name__ == '__main__':
    unittest.main()


class _StubCrew:
    def __init__(self, mapping):
        self._mapping = mapping

    def mentions(self, names):
        rendered, ids = [], []
        for name in names:
            found = self._mapping.get(name)
            if found:
                rendered.append('<@{}>'.format(found))
                ids.append(found)
            elif name:
                rendered.append(name)
        return rendered, ids


CREW = _StubCrew({'SpecialK': '429', 'GrandpaSzabo': '355'})


def _staffed_race(comms=(), tracker=None):
    return LeagueRace(
        start=datetime(2026, 9, 3, 20, 0, tzinfo=TIMEZONE),
        runner_one=_racer('SirLinkalot', '111'),
        runner_two=_racer('Windfox470', '222'),
        channel='Z1Rracing', comms=comms, tracker=tracker,
    )


class CrewTaggingTests(unittest.TestCase):
    def test_tags_comms_and_tracker_alongside_the_racers(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM, crew=CREW,
        )

        self.assertIn('<@429>', body['content'])
        self.assertIn('<@355>', body['content'])
        # Allow-listed, never parsed: the content is built from a live
        # spreadsheet, so a stray @everyone in a cell must not ping the server.
        self.assertEqual(body['allowed_mentions']['parse'], [])
        self.assertEqual(
            sorted(body['allowed_mentions']['users']), ['111', '222', '355', '429'],
        )

    def test_credits_unresolvable_crew_without_pinging_them(self):
        body = build_announcement(
            _staffed_race(comms=('Nobody',), tracker=None), ROOM, crew=CREW,
        )

        self.assertIn('Nobody', body['embeds'][0]['fields'][0]['value'])
        self.assertNotIn('<@>', body['content'])
        self.assertEqual(sorted(body['allowed_mentions']['users']), ['111', '222'])

    def test_says_nothing_extra_when_no_crew_is_scheduled(self):
        body = build_announcement(_staffed_race(), ROOM, crew=CREW)

        self.assertNotIn('Comms', body['content'])
        self.assertNotIn('Tracker', body['content'])
        self.assertEqual([field['name'] for field in body['embeds'][0]['fields']], ['Restream channel'])
        self.assertEqual(build_announcement(replace(_staffed_race(), channel=None), ROOM)['embeds'][0]['fields'], [])

    def test_works_with_no_crew_directory_at_all(self):
        # The control plane may never have been reachable. The announcement
        # still has to go out.
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM,
        )

        self.assertIn('SpecialK', body['embeds'][0]['fields'][0]['value'])
        self.assertEqual(sorted(body['allowed_mentions']['users']), ['111', '222'])

    def test_separates_the_crew_credits_with_a_blank_line(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM, crew=CREW,
        )

        # Matchup and crew are two separate thoughts; a single break renders
        # too tightly in Discord to scan at a glance.
        self.assertEqual(body['embeds'][0]['fields'][0]['value'], 'Comms: <@429>\nTracker: <@355>')
        self.assertEqual(body['embeds'][0]['fields'][1]['name'], 'Restream channel')


class ContinuationAnnouncementTests(unittest.TestCase):
    def test_warns_that_the_channel_is_already_on_air(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM,
            crew=CREW, continuation=True,
        )

        # The booth was not created for this race - the previous one is still
        # on air and the operator swaps the room and racers over. Crew opening
        # the booth mid-show should expect that rather than think it is broken.
        self.assertIn('already ON THE AIR', body['embeds'][0]['description'])

    def test_says_nothing_extra_for_an_ordinary_race(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM, crew=CREW,
        )

        self.assertNotIn('ON THE AIR', body['embeds'][0]['description'])

    def test_still_tags_the_crew_on_a_continuation(self):
        body = build_announcement(
            _staffed_race(comms=('SpecialK',), tracker='GrandpaSzabo'), ROOM,
            crew=CREW, continuation=True,
        )

        self.assertIn('<@429>', body['content'])
        self.assertEqual(sorted(body['allowed_mentions']['users']), ['111', '222', '355', '429'])


from unittest.mock import AsyncMock, Mock, patch

from ttpbot.league.coop import group_coop_matches
from ttpbot.league.matchups import Fixture

COOP_FIXTURE = Fixture(week=3, away='Bow Mode', home='Shadow Cartel', label='Week 3 - Coop Info Share')


def _away(name, discord_id):
    return Racer(sheet_name=name, team='BM', team_full='Bow Mode',
                 display_name=name, twitch_channel=name.lower(),
                 racetime_id='rt-' + name.lower(), discord_id=discord_id)


def _coop_match(tracker_one=None, tracker_two=None):
    start = datetime(2026, 9, 20, 20, 0, tzinfo=TIMEZONE)
    return group_coop_matches([
        LeagueRace(start=start, runner_one=_racer('SirLinkalot', '111'),
                   runner_two=_away('Windfox470', '222'), tracker=tracker_one,
                   game=1, fixture=COOP_FIXTURE),
        LeagueRace(start=start, runner_one=_away('seanfreston', '333'),
                   runner_two=_racer('Stags28', '444'), tracker=tracker_two,
                   game=1, fixture=COOP_FIXTURE),
    ], Mock())[0]


class CoopAnnouncementTests(unittest.TestCase):
    def test_names_both_teams_away_first(self):
        body = build_announcement(_coop_match(), ROOM)
        self.assertIn('<@222> & <@333> vs <@111> & <@444>', body['embeds'][0]['description'])
        self.assertEqual(body['embeds'][0]['title'], 'League Season 1 · Week 3 · Game 1 · Co-op')

    def test_allow_lists_all_four_runners(self):
        body = build_announcement(_coop_match(), ROOM)
        self.assertEqual(body['allowed_mentions'], {'parse': [], 'users': ['111', '222', '333', '444']})

    def test_credits_both_trackers_when_the_rows_differ(self):
        body = build_announcement(_coop_match('droois', 'ISUMatt'), ROOM)
        self.assertIn('Tracker: droois, ISUMatt', body['embeds'][0]['fields'][0]['value'])

    def test_the_1v1_post_uses_the_same_layout(self):
        race = _race(_racer('SirLinkalot', '111'), _racer('Windfox470', '222'))
        card = build_announcement(race, ROOM)['embeds'][0]
        self.assertIn('<@111> vs <@222>', card['description'])
        self.assertIn('[Race Room](' + ROOM + ')', card['description'])


class AnnouncementLayoutTests(unittest.TestCase):
    def test_week_flagset_time_and_links_share_one_clear_card(self):
        race = replace(_staffed_race(), game=2, fixture=Fixture(6, 'Shadow Cartel', 'The Missing Links', 'Consternation'))
        thread = 'https://discord.com/channels/123/456'
        body = build_announcement(race, ROOM, matchup_url=thread)
        card = body['embeds'][0]
        self.assertEqual(card['title'], 'League Season 1 · Week 6 · Game 2')
        self.assertIn('**<@111> vs <@222>**', card['description'])
        self.assertIn('Consternation', card['description'])
        self.assertIn('**Starts:** <t:', card['description'])
        self.assertIn('[Race Room]({}) | [Matchup Thread]({})'.format(ROOM, thread), card['description'])
        self.assertNotIn('Open race room', card['description'])


class AnnouncementLinkDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_thread_lookup_still_posts_the_room(self):
        from ttpbot.league.announce import send_league_announcement
        race = replace(_staffed_race(), fixture=Fixture(6, 'Shadow Cartel', 'The Missing Links', 'Consternation'))
        threads = Mock(configured=True, matchup_url=AsyncMock(side_effect=asyncio.TimeoutError()))
        response = AsyncMock()
        response.__aenter__.return_value = Mock(status=204)
        with patch('ttpbot.league.announce.aiohttp.request', return_value=response) as request:
            sent = await send_league_announcement(race, ROOM, 'https://example.test/webhook', Mock(), threads=threads)
        self.assertTrue(sent)
        description = request.call_args.kwargs['json']['embeds'][0]['description']
        self.assertIn('[Race Room](' + ROOM + ')', description)
        self.assertNotIn('Matchup Thread', description)

    def test_room_link_is_inside_one_rich_card_without_an_automatic_preview(self):
        body = build_announcement(_staffed_race(comms=('SpecialK',)), ROOM, crew=CREW)
        self.assertNotIn(ROOM, body['content'])
        self.assertEqual(len(body['embeds']), 1)
        card = body['embeds'][0]
        self.assertIn('[Race Room](' + ROOM + ')', card['description'])
        self.assertTrue(card['title'].startswith('League Season 1'))
        self.assertEqual([field['name'] for field in card['fields']], ['Restream crew', 'Restream channel'])

    def test_room_and_each_broadcast_detail_have_their_own_line(self):
        body = build_announcement(_staffed_race(comms=('SpecialK', 'GrandpaSzabo'), tracker='Other'), ROOM, crew=CREW)
        self.assertEqual(body['content'], '<@111> <@222> <@355> <@429>')
        self.assertEqual(body['embeds'][0]['fields'], [
            {'name': 'Restream crew', 'value': 'Comms: <@429>, <@355>\nTracker: Other'},
            {'name': 'Restream channel', 'value': '[Z1Rracing](https://www.twitch.tv/z1rracing)'},
        ])

    def test_coop_announces_both_assigned_channels_without_repeating_one(self):
        match = _coop_match()
        match = replace(match, rows=tuple(replace(row, channel=channel) for row,channel in zip(match.rows, ('Z1Rracing', 'Z1Rracing2'))))
        body = build_announcement(match, ROOM)['embeds'][0]['fields'][0]['value']
        self.assertEqual('[Z1Rracing](https://www.twitch.tv/z1rracing), [Z1Rracing2](https://www.twitch.tv/z1rracing2)', body)
        self.assertNotIn('Comms:', body)
        self.assertNotIn('Tracker:', body)
        same = replace(match, rows=tuple(replace(row, channel='Z1Rracing') for row in match.rows))
        self.assertEqual(build_announcement(same, ROOM)['embeds'][0]['fields'][0]['value'].count('https://www.twitch.tv/z1rracing'), 1)
