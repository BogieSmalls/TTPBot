import unittest

from ttpbot.flag_summary import (
    MESSAGE_LIMIT,
    FlagStringError,
    format_summary,
    summary_lines,
)

# TTP4 Random% Remastered: close to the worst case for length.
RANDOM_PERCENT = '24hJoDaoq92qaumIfio4Qq8LtfU0Xt8tpG3Iafo'
# The 3v3 Week 6 flagset from the 2026-09-25 race room that prompted !summary.
WEEK_SIX = 'CKnGaCG0jI3PvaGohjRZIOxiM8Y9W8GjoIpZfdC'


class FlagSummaryTests(unittest.TestCase):
    def test_rejects_characters_no_flag_string_contains(self):
        with self.assertRaises(FlagStringError):
            format_summary('not-a-flag-string')
        with self.assertRaises(FlagStringError):
            format_summary('')

    def test_matches_what_racers_read_off_the_randomizer(self):
        # Crump read these off the randomizer by hand in that room.
        joined = ' '.join(format_summary(WEEK_SIX))
        for expected in (
            'Quest: 1st Quest', 'Dungeon Quest: Shapes', 'Item Shuffle: Item Only',
            'Enemy HP: +/- 2', 'Boss HP: +/- 2', 'A/C/WS: Bow / Recorder / Ladder',
            'Max Start Items: 1', 'White Sword Hearts: 4-4',
            'Magical Sword Hearts: 14-14', 'L9 Items: Red Ring / Silver Arrow',
            'Extra Candles', 'Book: Atlas', 'Start Wood Arrow', '2Q Rooms',
            'Bridges to the River', 'Lost Woods Shortcut',
        ):
            self.assertIn(expected, joined)

    def test_strips_settings_true_of_nearly_every_race(self):
        lines = summary_lines(RANDOM_PERCENT)
        self.assertNotIn('Race ROM', lines)
        self.assertNotIn('Speed Up Text', lines)
        self.assertNotIn('Starting Triforce: 0', lines)

    def test_mirrors_the_randomizer_summary_tab(self):
        joined = ' '.join(format_summary(RANDOM_PERCENT))
        for expected in (
            'Quest: Random', 'Dungeon Quest: Shapes', 'Hints: Random',
            'Start Screen: Full start shuffle', 'Item Shuffle: Full',
            'A/C/WS: Random / Random / Random', 'Starting Hearts: 4',
            'White Sword Hearts: 5-6', 'Allow Impt Items in 9', 'RMOS',
            'Book To Understand Old Men', 'Force OW Block', "Don't Sort Shapes",
            '2Q Rooms', '2Q Monsters', 'Dungeon Start Room', '8 Bombs', 'Wood Boom.',
        ):
            self.assertIn(expected, joined)
        for absent in (
            'Recorder To New Dungeons', 'Wood Sword Cave', 'Dungeon Drops',
            'Hungry Goriya', 'MMG', 'Item Sprites', 'Triforce Range',
        ):
            self.assertNotIn(absent, joined)

    def test_collects_possible_flags_under_one_label(self):
        joined = ' '.join(format_summary(RANDOM_PERCENT))
        self.assertIn('Possible: ', joined)
        self.assertIn('Possible Start: ', joined)
        self.assertNotIn('Extra Candles: Possible', joined)

    def test_names_a_known_preset_and_numbers_messages(self):
        messages = format_summary(RANDOM_PERCENT)
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[0].startswith('Flags (ttp4rp) 1/2: '))
        self.assertTrue(messages[1].startswith('Flags (ttp4rp) 2/2: '))

    def test_names_no_preset_on_a_near_miss(self):
        messages = format_summary(RANDOM_PERCENT[:-1] + '1')
        self.assertNotIn('(', messages[0].split(':')[0])

    def test_single_message_is_unnumbered(self):
        self.assertTrue(format_summary(WEEK_SIX)[0].startswith('Flags: '))

    def test_never_exceeds_the_message_limit(self):
        for flags in (RANDOM_PERCENT, WEEK_SIX):
            for message in format_summary(flags):
                self.assertLessEqual(len(message), MESSAGE_LIMIT)


if __name__ == '__main__':
    unittest.main()
