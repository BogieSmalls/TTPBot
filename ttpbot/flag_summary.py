"""Decode a Z1R flag string into a short, chat-sized summary.

A Python port of the Z1RR.Restream !flags decoder (mini/server/race-info/
flag-codec.ts, flag-definitions.ts, flag-summary.ts and
chat/commands/flags.ts). Keep the tables in step with that project: they
mirror the randomizer's own option lists, and the order of every list is
load-bearing -- the flag string is one big mixed-radix number read off in
exactly this order.
"""

from .config import SEED_PRESETS

FLAG_ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!'
FLAG_BASE = 63

# Kept under racetime's chat limit with room to spare. A heavier flagset
# spills into another message rather than losing content.
MESSAGE_LIMIT = 500

_ITEMS = (
    'Random', 'Book', 'Boomerang', 'Bow', 'Heart Container', 'Ladder',
    'Magical Boomerang', 'Magical Key', 'Power Bracelet', 'Raft', 'Recorder',
    'Red Candle', 'Red Ring', 'Silver Arrow', 'Wand', 'White Sword', 'Non-Heart',
)

# (name, slots, options). Slots can exceed the option count: the randomizer
# reserves room for options it has not shipped yet.
COMBOS = (
    ('questNumber', 8, ('1st Quest', '2nd Quest', 'Mixed Quest - 1st', 'Mixed Quest - 2nd', 'Random')),
    ('dungeonQuest', 10, ('1st Quest', '2nd Quest', 'Mixed Quest', 'Shapes', 'Mixed + Shapes', 'Random No Shapes', 'Random')),
    ('hintType', 10, ('Normal', 'Helpful', 'Community', 'Deception', 'Mixed', 'Blank', 'Random')),
    ('startScreenBox', 7, ('Normal', 'Easy start shuffle', 'Full start shuffle', 'Wood Sword Screen')),
    ('cavesToShuffle', 8, ('Vanilla', 'Dungeons Only', 'Non-dungeons Only', 'All Caves', 'Random')),
    ('shuffleItemsBox', 8, ('None', 'Item Only', 'Intra-Dungeon', 'Full', 'Random')),
    ('woodSwordState', 8, ('Normal', 'Wood Sword Only', 'No Wood Sword', 'Swordless', 'Random')),
    ('triforcesRequired', 15, (
        '8 triforces', '7 triforces', '6 triforces', '5 triforces', '4 triforces',
        '3 triforces', '2 triforces', '1 triforce', 'Open Level 9', 'Triforce Range',
        'Specific Triforces', 'Other Item', 'Random',
    )),
    ('enemyHPBox', 8, ('Normal', '+/- 2', '+/- 4', '0 HP', 'Random')),
    ('bossHPBox', 8, ('Normal', '+/- 2', '+/- 4', '0 HP', 'Random')),
    ('armosSelection', 20, _ITEMS),
    ('coastSelection', 20, _ITEMS),
    ('whiteSwordSelection', 20, _ITEMS),
    ('dungeonRoomShuffle', 7, ('Vanilla', 'In-Dungeon Shuffle', 'Full Shuffle', 'Random')),
    ('itemSpriteBox', 7, ('Normal', 'Fun %', 'Sprite Shuffle', 'Random')),
    ('startHearts', 20, tuple(str(n) for n in range(1, 17)) + ('1-5 Hearts',)),
    ('maxStartItems', 26, ('All',) + tuple(str(n) for n in range(21)) + ('Random',)),
    ('startTriforce', 13, tuple(str(n) for n in range(9)) + ('Random',)),
    ('redBubble', 7, ('Swordless', 'Inverted Control', 'Slow Speed', 'Random')),
    ('wsMin', 6, ('4', '5', '6')),
    ('wsMax', 6, ('6', '5', '4')),
    ('msMin', 8, ('10', '11', '12', '13', '14')),
    ('msMax', 8, ('14', '13', '12', '11', '10')),
    ('triforceRangeLow', 12, tuple(str(n) for n in range(9))),
    ('triforceRangeHigh', 12, tuple(str(n) for n in range(8, -1, -1))),
    ('itemIn9_1', 20, _ITEMS),
    ('itemIn9_2', 20, _ITEMS),
)

# (name, label), each a tri-state: off / on / possible.
CHECKBOXES = (
    ('UpdateWhistle', 'Recorder To New Dungeons'),
    ('shuffleWoodSword', 'Shuffle Wood Sword Cave'),
    ('woodBlocks', 'Allow OW Blocks of Wood Sword'),
    ('shuffleBraceletCaves', 'Shuffle Take Any Road Caves'),
    ('changeSecretSpots', 'Change Secret Spots'),
    ('mirrorOW', 'Mirror Overworld'),
    ('reverseWhistle', 'Recorder to Unbeaten Dungeons'),
    ('ShuffleShopItems', 'Shuffle Shop Items'),
    ('AddExtraCandles', 'Extra Candles'),
    ('changeSwordHearts', 'Change Sword Hearts'),
    ('shuffleMMG', 'Change MMG'),
    ('bombUpgrades', 'Change Bomb Upgrades'),
    ('shuffleArmos', 'Shuffle Armos'),
    ('ShuffleDungeonItems', 'Shuffle Dungeon Items'),
    ('shuffleDungeonDrops', 'Shuffle Dungeon Drops'),
    ('dungeonHearts', 'Shuffle Dungeon Hearts'),
    ('minorDungeonDrops', 'Shuffle Minor Dungeon Drops'),
    ('forceGannon', 'Force Gannon Fight'),
    ('shuffleDungeonText', 'Shuffle Dungeon Text'),
    ('add2ndQuestRooms', 'Add 2nd Quest Rooms'),
    ('add2ndQuestDoors', 'Add 2nd Quest Doors'),
    ('addMoneyOrLife', 'Add Money or Life Rooms'),
    ('changeLifeOrMoney', 'Change Leave Your Rooms'),
    ('hideDungeonNumbers', 'Hide Dungeon Numbers'),
    ('anyItemIn9', 'Allow Important Items in 9'),
    ('startRoomSwap', 'Shuffle Dungeon Start Room'),
    ('dungeonPalette', 'Shuffle Dungeon Palettes'),
    ('shuffleGoriya', 'Shuffle Hungry Goriya'),
    ('shuffleBombMen', 'Shuffle Bomb Upgrade Men'),
    ('removeOpenStairs', 'Remove Most Open Stairs'),
    ('shuffleOverworld', 'Shuffle Overworld Monsters'),
    ('shuffleDungeonMonsters', 'Shuffle Dungeon Monsters'),
    ('shuffleGanonZelda', 'Shuffle Gannon and Zelda'),
    ('shuffleBosses', 'Shuffle Bosses'),
    ('includeLevel9', 'Shuffle Level 9 Monsters'),
    ('shuffleMonstersLevels', 'Shuffle Monsters Between Levels'),
    ('add2ndQuestMonsters', 'Add 2nd Quest Monsters'),
    ('enemyDropGroups', 'Shuffle Enemy Drop Groups'),
    ('itemMadness', 'Make Important Items Drops'),
    ('shuffleBossGroups', 'Randomize Boss Groups'),
    ('gannonHPto0', 'Set Gannon HP to 0'),
    ('shuffleEnemyGroups', 'Shuffle Enemy Groups'),
    ('shuffleOverworldEnemies', 'Shuffle Overworld Group'),
    ('itemDropRate', 'Randomize Item Drop Rate'),
    ('maxEnemyHealth', 'Maximum Enemy Health'),
    ('maxBossHealth', 'Maximum Boss Health'),
    ('bookBomb', 'Replace Book Fire with Explosion'),
    ('translateOldMen', 'Book To Understand Old Men'),
    ('bookAtlas', 'Book is an Atlas'),
    ('blackout', 'Blackout'),
    ('printDebug', 'Print Quest Info'),
    ('permaBeam', 'Permanent Sword Beam'),
    ('OHKO', 'OHKO'),
    ('speedUpText', 'Speed Up Text'),
    ('hideHealth', 'Hidden Health'),
    ('killItems', 'Killable Drop Items'),
    ('startRaft', 'Start Raft'),
    ('startBow', 'Start Bow'),
    ('startBombs', 'Start 8 Bombs'),
    ('startRecorder', 'Start Recorder'),
    ('startBait', 'Start Bait'),
    ('startAnyKey', 'Start Magical Key'),
    ('startLetter', 'Start Letter'),
    ('startPB', 'Start Power Bracelet'),
    ('startLadder', 'Start Ladder'),
    ('startWand', 'Start Wand'),
    ('startBook', 'Start Book'),
    ('startWoodSword', 'Start Wood Sword'),
    ('startWhiteSword', 'Start White Sword'),
    ('startMagicalSword', 'Start Magical Sword'),
    ('startWoodArrow', 'Start Wood Arrow'),
    ('startSilverArrow', 'Start Silver Arrow'),
    ('startBlueCandle', 'Start Blue Candle'),
    ('startRedCandle', 'Start Red Candle'),
    ('startBlueRing', 'Start Blue Ring'),
    ('startRedRing', 'Start Red Ring'),
    ('startWoodBoomerang', 'Start Wood Boomerang'),
    ('startMagicBoomerang', 'Start Magical Boomerang'),
    ('addExtraBosses', 'Add Extra Bosses'),
    ('rupeeSpeed', 'Rupees Affect Speed'),
    ('overworldItemBlock', 'Force Overworld Block'),
    ('tourneyMode', 'Race ROM'),
    ('hideNumbers', 'Hidden Numbers'),
    ('preventClips', 'Prevent Clipping'),
    ('startMagicShield', 'Start Magical Shield'),
    ('noSortShapes', 'Do Not Sort Shapes'),
    ('addBridges', 'Add Bridges to the River'),
    ('lostWoodsShortcut', 'Add Lost Woods Shortcut'),
    ('owVisibleTiles', 'Change Hidden OW Tiles'),
    ('bombCapacityUp', 'Increase Bomb Capacity Upgrade'),
    ('altDrops', 'Allow Universal Drops in Shapes'),
)

_CHECKBOX_STATES = ('off', 'on', 'possible')

# Settings nearly every race uses. Saying them costs characters without
# telling anyone anything.
COMMON_LINES = frozenset({
    'Hints: Normal', 'Start Screen: Normal', 'Enemy HP: Normal', 'Boss HP: Normal',
    'Dungeon Rooms: Vanilla', 'Item Sprites: Normal', 'Starting Hearts: 3',
    'Max Start Items: All', 'Starting Triforce: 0', 'Wood Sword: Normal',
    'Start Screen: Easy start shuffle', 'Triforces Required: 8 triforces',
    'Force Gannon Fight', 'Caves: All Caves', 'Dungeon Rooms: Full Shuffle',
    'Shuffle Shop Items', 'Randomize Boss Groups', 'Red Bubble: Swordless',
    'Shuffle Dungeon Items', 'Shuffle Dungeon Start Room', 'Shuffle Enemy Groups',
    'Shuffle Overworld Group', 'White Sword Hearts: 4-6',
    'Magical Sword Hearts: 10-14', 'Triforce Range: 0-8',
    'Race ROM', 'Speed Up Text',
})

# Flags the randomizer's Summary tab never prints (per updateSummary() in the
# decompiled v3.6.5 MyForm.cs). A denylist on purpose: an allowlist built from
# sample output once dropped Blackout, OHKO and Mirror Overworld.
SUMMARY_NEVER_PRINTS = frozenset({
    'Change MMG', 'Change Bomb Upgrades', 'Shuffle Armos', 'Shuffle Dungeon Items',
    'Shuffle Dungeon Drops', 'Shuffle Dungeon Hearts', 'Shuffle Dungeon Text',
    'Add Money or Life Rooms', 'Change Leave Your Rooms', 'Shuffle Dungeon Palettes',
    'Shuffle Hungry Goriya', 'Shuffle Bomb Upgrade Men', 'Shuffle Overworld Monsters',
    'Shuffle Dungeon Monsters', 'Shuffle Gannon and Zelda', 'Shuffle Bosses',
    'Shuffle Level 9 Monsters', 'Shuffle Monsters Between Levels',
    'Randomize Item Drop Rate', 'Print Quest Info', 'Speed Up Text', 'Race ROM',
    'Allow Universal Drops in Shapes',
    # Only decides whether the White Sword range prints; never printed itself.
    'Change Sword Hearts',
})

# The Summary lists these only when Cave Shuffle is "Dungeons Only".
CAVE_SHUFFLE_DETAIL = frozenset({
    'Recorder To New Dungeons', 'Shuffle Wood Sword Cave', 'Shuffle Take Any Road Caves',
})

# The Summary omits the starting-items block entirely when nothing can start.
NO_START_ITEMS_LINE = 'Max Start Items: 0'

# Applied in order, longest phrase first so a shorter rule cannot pre-empt one.
ABBREVIATIONS = (
    ('Remove Most Open Stairs', 'RMOS'),
    ('Book is an Atlas', 'Book: Atlas'),
    ('Important', 'Impt'),
    ('2nd Quest', '2Q'),
    ('Overworld', 'OW'),
    ('Boomerang', 'Boom.'),
    ('Do Not', "Don't"),
)

# Repeated openings worth writing once.
VERBS = ('Shuffle', 'Change', 'Add')


class FlagStringError(ValueError):
    """The flag string holds a character no flag string can contain."""


def decode(flag_string):
    """Return (combos, checkboxes): name -> label, and name -> tri-state."""
    if not flag_string:
        raise FlagStringError('Flag string is empty')

    n = 0
    for ch in flag_string:
        idx = FLAG_ALPHABET.find(ch)
        if idx < 0:
            raise FlagStringError(f'Invalid character in flag string: {ch!r}')
        n = n * FLAG_BASE + idx

    combos = {}
    for name, slots, options in COMBOS:
        n, raw = divmod(n, slots)
        combos[name] = options[min(raw, len(options) - 1)]

    checkboxes = {}
    for name, _label in CHECKBOXES:
        n, raw = divmod(n, 3)
        checkboxes[name] = _CHECKBOX_STATES[raw]

    return combos, checkboxes


def decoded_lines(combos, checkboxes):
    """Every setting as a line, in the order the randomizer lists them."""
    c = combos
    lines = [
        f"Quest: {c['questNumber']}",
        f"Dungeon Quest: {c['dungeonQuest']}",
        f"Hints: {c['hintType']}",
        f"Start Screen: {c['startScreenBox']}",
        f"Caves: {c['cavesToShuffle']}",
        f"Item Shuffle: {c['shuffleItemsBox']}",
        f"Wood Sword: {c['woodSwordState']}",
        f"Triforces Required: {c['triforcesRequired']}",
        f"Enemy HP: {c['enemyHPBox']}",
        f"Boss HP: {c['bossHPBox']}",
        f"A/C/WS: {c['armosSelection']} / {c['coastSelection']} / {c['whiteSwordSelection']}",
        f"Dungeon Rooms: {c['dungeonRoomShuffle']}",
        f"Item Sprites: {c['itemSpriteBox']}",
        f"Starting Hearts: {c['startHearts']}",
        f"Max Start Items: {c['maxStartItems']}",
        f"Starting Triforce: {c['startTriforce']}",
        f"Red Bubble: {c['redBubble']}",
        f"White Sword Hearts: {c['wsMin']}-{c['wsMax']}",
        f"Magical Sword Hearts: {c['msMin']}-{c['msMax']}",
        f"Triforce Range: {c['triforceRangeLow']}-{c['triforceRangeHigh']}",
        f"L9 Items: {c['itemIn9_1']} / {c['itemIn9_2']}",
    ]
    for name, label in CHECKBOXES:
        state = checkboxes[name]
        if state == 'on':
            lines.append(label)
        elif state == 'possible':
            lines.append(f'{label}: Possible')
    return lines


def _summary_prints(line, cave_shuffle):
    if line == NO_START_ITEMS_LINE:
        return False
    # "<label>: Possible" is a tri-state checkbox; the label is what matters.
    label = line[:-len(': Possible')] if line.endswith(': Possible') else line
    if label in SUMMARY_NEVER_PRINTS:
        return False
    if label in CAVE_SHUFFLE_DETAIL:
        return cave_shuffle == 'Dungeons Only'
    return True


def _abbreviate(line):
    for long, short in ABBREVIATIONS:
        line = line.replace(long, short)
    return line


def summary_lines(flag_string):
    """The lines worth saying in chat: filtered, then abbreviated."""
    combos, checkboxes = decode(flag_string)
    cave_shuffle = combos['cavesToShuffle']
    return [
        # Filter before abbreviating, which rewrites labels the sets match on.
        _abbreviate(line)
        for line in decoded_lines(combos, checkboxes)
        if line not in COMMON_LINES and _summary_prints(line, cave_shuffle)
    ]


def preset_name(flag_string):
    """The !race preset this flag string is, on an exact match only."""
    for name, flags in SEED_PRESETS.items():
        if flags == flag_string:
            return name
    return None


def _items(lines):
    """(group label or None, value) pairs, standalone values first."""
    possible = [line[:-len(': Possible')] for line in lines if line.endswith(': Possible')]
    definite = [line for line in lines if not line.endswith(': Possible')]

    def verb_of(line):
        return next((v for v in VERBS if line.startswith(f'{v} ') and ':' not in line), None)

    items = [(None, line) for line in definite if verb_of(line) is None]
    for verb in VERBS:
        items += [(verb, line[len(verb) + 1:]) for line in definite if verb_of(line) == verb]

    items += [
        ('Possible', value) for value in possible
        if not any(value.startswith(f'{v} ') for v in VERBS) and not value.startswith('Start ')
    ]
    for prefix in VERBS + ('Start',):
        items += [
            (f'Possible {prefix}', value[len(prefix) + 1:])
            for value in possible if value.startswith(f'{prefix} ')
        ]
    return items


def _heading(index, total, preset):
    named = f'Flags ({preset})' if preset else 'Flags'
    return f'{named}: ' if total == 1 else f'{named} {index}/{total}: '


def _attempt(items, total, preset, limit):
    out = []
    current = ''
    group = None
    for label, value in items:
        budget = limit - len(_heading(len(out) + 1, total, preset))
        opening = f'{label}: ' if label and label != group else ''
        separator = (' · ' if opening else ', ') if current else ''
        addition = f'{separator}{opening}{value}'

        if len(current) + len(addition) <= budget:
            current += addition
            group = label
            continue

        out.append(current)
        group = label
        # A continued group repeats its label so the next message stands alone.
        current = f'{label}: {value}' if label else value
        if len(current) > budget:
            return None
    if current:
        out.append(current)
    return out


def format_summary(flag_string, limit=MESSAGE_LIMIT):
    """Chat messages summarising a flag string.

    Raises FlagStringError if the string cannot be decoded.
    """
    lines = summary_lines(flag_string)
    preset = preset_name(flag_string)
    items = _items(lines)
    if not items:
        return [f"{_heading(1, 1, preset)}standard settings"]
    for total in range(1, 9):
        messages = _attempt(items, total, preset, limit)
        if messages and len(messages) == total:
            return [
                f'{_heading(i, total, preset)}{body}'
                for i, body in enumerate(messages, start=1)
            ]
    raise FlagStringError('Flag summary does not fit in chat')
