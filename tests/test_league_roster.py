import unittest

from ttpbot.league.roster import (
    Racer,
    Roster,
    RosterError,
    UnknownRacerError,
    load_roster,
    strip_team_prefix,
)


def _racer(sheet_name, team='SC', racetime_id='abc123', discord_id='1'):
    return Racer(
        sheet_name=sheet_name,
        team=team,
        team_full='Shadow Cartel',
        display_name=sheet_name,
        twitch_channel=sheet_name.lower(),
        racetime_id=racetime_id,
        discord_id=discord_id,
    )


class StripTeamPrefixTests(unittest.TestCase):
    def test_strips_team_prefix_and_whitespace(self):
        self.assertEqual(strip_team_prefix('(SC) Droois'), 'Droois')

    def test_strips_multiword_team_prefix(self):
        self.assertEqual(strip_team_prefix('(8BW) SirLinkalot'), 'SirLinkalot')

    def test_leaves_unprefixed_names_alone(self):
        self.assertEqual(strip_team_prefix('Droois'), 'Droois')

    def test_collapses_internal_whitespace(self):
        self.assertEqual(strip_team_prefix('  (F451)   Rhinohero  '), 'Rhinohero')

    def test_does_not_strip_parenthetical_that_is_not_a_prefix(self):
        self.assertEqual(strip_team_prefix('droois (grindhalo)'), 'droois (grindhalo)')


class RosterResolutionTests(unittest.TestCase):
    def setUp(self):
        self.roster = Roster([_racer('Droois'), _racer('BlessedBe', team='TUG')])

    def test_resolves_exact_name(self):
        self.assertEqual(self.roster.resolve('Droois').sheet_name, 'Droois')

    def test_resolution_is_case_insensitive(self):
        self.assertEqual(self.roster.resolve('droois').sheet_name, 'Droois')

    def test_resolves_through_team_prefix(self):
        self.assertEqual(self.roster.resolve('(SC) Droois').sheet_name, 'Droois')

    def test_unknown_name_raises_rather_than_guessing(self):
        # Never fuzzy-match: inviting the wrong racer is worse than no room.
        with self.assertRaises(UnknownRacerError):
            self.roster.resolve('(SC) Drooi')

    def test_blank_cell_raises(self):
        with self.assertRaises(UnknownRacerError):
            self.roster.resolve('   ')

    def test_duplicate_sheet_names_are_rejected(self):
        with self.assertRaises(RosterError):
            Roster([_racer('Droois'), _racer('droois')])


class ShippedRosterTests(unittest.TestCase):
    def setUp(self):
        self.roster = load_roster()

    def test_ships_all_forty_two_racers(self):
        self.assertEqual(len(self.roster), 42)

    def test_droois_resolves_to_grindhalo_never_droorace(self):
        # crop_profiles holds both "droois (grindhalo)" and "droois (droorace)";
        # only grindhalo is on the League roster.
        droois = self.roster.resolve('(SC) Droois')
        self.assertEqual(droois.twitch_channel, 'grindhalo')
        self.assertEqual(droois.racetime_id, 'NqO2YoLLAbo9QEya')

    def test_resolves_the_six_names_that_differ_from_crop_profiles(self):
        for cell in ('(8BW) Deus Ex Machina', '(F451) Birdman', '(TML) Merks',
                     '(TBC) Sigil', '(TUG) BlessedBe', '(SC) Droois'):
            with self.subTest(cell=cell):
                self.assertIsNotNone(self.roster.resolve(cell).racetime_id)

    def test_every_racer_has_a_racetime_id(self):
        self.assertTrue(all(r.racetime_id for r in self.roster))

    def test_every_racer_has_a_discord_id(self):
        self.assertTrue(all(r.discord_id for r in self.roster))

    def test_racetime_ids_are_unique(self):
        ids = [r.racetime_id for r in self.roster]
        self.assertEqual(len(set(ids)), len(ids))

    def test_roster_is_fourteen_teams_of_three(self):
        teams = {}
        for racer in self.roster:
            teams.setdefault(racer.team, []).append(racer)
        self.assertEqual(len(teams), 14)
        self.assertEqual({len(v) for v in teams.values()}, {3})

    def test_display_name_matches_sheet_name_for_every_racer(self):
        # The room title (league/schedule.py) is built from display_name,
        # but restart recovery (league/roster.py via handler._league_invite_ids)
        # resolves that title's names through the sheet_name index. Nothing
        # in the type system enforces display_name == sheet_name, so a
        # divergent roster edit would silently make resolution fail and
        # invite nobody. Guard the invariant here instead.
        for racer in self.roster:
            with self.subTest(sheet_name=racer.sheet_name):
                self.assertIs(self.roster.resolve(racer.display_name), racer)

    def test_no_display_name_contains_the_title_separator(self):
        # The title fallback in handler._league_invite_ids() splits the
        # room title on ' vs. '; a display_name containing that literal
        # substring would make the split ambiguous.
        for racer in self.roster:
            with self.subTest(display_name=racer.display_name):
                self.assertNotIn(' vs. ', racer.display_name)


if __name__ == '__main__':
    unittest.main()
