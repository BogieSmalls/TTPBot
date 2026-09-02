import os
from datetime import date, time
from zoneinfo import ZoneInfo

SEASON_START = date(2026, 8, 31)
SEASON_END = date(2026, 12, 19)

TIMEZONE = ZoneInfo("America/New_York")

GOAL_NAME = "TTP Season 5"
POST_SEASON_GOAL_NAME = "Beat the game"

REGULAR_SEASON_ROOM_INFO_PREFIX = "TTP Season 5"
POST_SEASON_ROOM_INFO_PREFIX = "Triforce Triple Play"
TTP_ROOM_INFO_PREFIXES = (
    REGULAR_SEASON_ROOM_INFO_PREFIX,
    POST_SEASON_ROOM_INFO_PREFIX,
)

# Z1RR League rooms. Separate from the TTP prefixes above: League rooms
# share the "Beat the game" goal, so this prefix is what distinguishes them.
LEAGUE_ROOM_INFO_PREFIX = "League: "

# Default League schedule spreadsheet. Lives here (not in ttpbot.league)
# so core code (runtime_config.py) does not need to import the League
# feature package just to resolve startup configuration.
DEFAULT_SCHEDULE_URL = (
    'https://docs.google.com/spreadsheets/d/'
    '1MEyO03Wib6iyH7-75e-orh2K75AATwEoJB9HlTe9VgM/export'
    '?format=csv&gid=2033319762'
)

ROOM_OPEN_MINUTES_BEFORE = 30
WEBHOOK_MINUTES_BEFORE = 20  # Post webhook 20 min before race (10 min after room opens)

def _env_or_default(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip() or default


Z1RR_DISCORD_URL = _env_or_default(
    'TTPBOT_Z1RR_DISCORD_URL',
    'https://discord.gg/MX6EB26HYB',
)
Z1RR_RACEROOM_URL = _env_or_default(
    'TTPBOT_Z1RR_RACEROOM_URL',
    'https://raceroom.z1rracing.com/z1rr',
)

# Map race time -> (TTP number, day name uses previous day)
# Evening slate: 8 PM=TTP1, 10 PM=TTP2, 12 AM=TTP3
# Saturday slate: 12 PM=TTP1, 3 PM=TTP2, 6 PM=TTP3
RACE_NUMBER_MAP = {
    time(20, 0): (1, False),  # TTP1, same day
    time(22, 0): (2, False),  # TTP2, same day
    time(0, 0):  (3, True),   # TTP3, use previous day's name
    time(12, 0): (1, False),  # TTP1 (Saturday)
    time(15, 0): (2, False),  # TTP2 (Saturday)
    time(18, 0): (3, False),  # TTP3 (Saturday)
}

# (minutes_before_start, message)
REMINDER_SCHEDULE = [
    (10, "Reminder: Please roll the seed if you haven't already!"),
    (5, "5 minutes to race time! Please get your streams ready and ready up!"),
    (1, "1 minute to race time! Please get your streams ready and ready up!"),
    (0, "Race time! Please ready up. Let's be respectful of everyone's time."),
]

# Race times by day of week (0=Monday, 6=Sunday), in US/Eastern.
# The 12:00 AM slots on Tue-Sat are the "midnight" races following the
# previous evening's session (e.g. Monday's 3rd race lands on Tuesday 00:00).
WEEKLY_SCHEDULE = {
    0: [time(20, 0), time(22, 0)],                          # Monday
    1: [time(0, 0), time(20, 0), time(22, 0)],              # Tuesday
    2: [time(0, 0), time(20, 0), time(22, 0)],              # Wednesday
    3: [time(0, 0), time(20, 0), time(22, 0)],              # Thursday
    4: [time(0, 0), time(20, 0), time(22, 0)],              # Friday
    5: [time(0, 0), time(12, 0), time(15, 0), time(18, 0)], # Saturday
    6: [],                                                    # Sunday
}

# Hash item alias map: lowercase alias -> canonical name.
# Canonical names also included (lowercased) for direct matching.
HASH_ALIASES = {
    # Canonical names (single-word, case-insensitive)
    'anykey': 'AnyKey',
    'arrow': 'Arrow',
    'beams': 'Beams',
    'bluecandle': 'BlueCandle',
    'bluepotion': 'BluePotion',
    'bluering': 'BlueRing',
    'bomb': 'Bomb',
    'book': 'Book',
    'bow': 'Bow',
    'clock': 'Clock',
    'compass': 'Compass',
    'fairy': 'Fairy',
    'heart': 'Heart',
    'key': 'Key',
    'ladder': 'Ladder',
    'letter': 'Letter',
    'link': 'Link',
    'magicboomer': 'MagicBoomer',
    'mags': 'Mags',
    'meat': 'Meat',
    'merchant': 'Merchant',
    'oldman': 'OldMan',
    'pb': 'PB',
    'raft': 'Raft',
    'redcandle': 'RedCandle',
    'redpotion': 'RedPotion',
    'redring': 'RedRing',
    'rupee': 'Rupee',
    'shield': 'Shield',
    'silvers': 'Silvers',
    'smallheart': 'SmallHeart',
    'tooter': 'Tooter',
    'triforce': 'Triforce',
    'wand': 'Wand',
    'whitesword': 'WhiteSword',
    'woodenboomer': 'WoodenBoomer',
    'woodsword': 'WoodSword',
    # Player aliases
    'ak': 'AnyKey',
    'animal': 'AnyKey',
    'arrows': 'Arrow',
    'beam': 'Beams',
    'wifi': 'Beams',
    'candle': 'BlueCandle',
    'flames': 'BlueCandle',
    'potion': 'BluePotion',
    'ring': 'BlueRing',
    'bombs': 'Bomb',
    'books': 'Book',
    'bows': 'Bow',
    'd': 'Bow',
    'clocks': 'Clock',
    'fairies': 'Fairy',
    'cj': 'Heart',
    'hearts': 'Heart',
    'keys': 'Key',
    'letters': 'Letter',
    'note': 'Letter',
    'bait': 'Meat',
    'bracelet': 'PB',
    'rupees': 'Rupee',
    'ruppee': 'Rupee',
    'ruppees': 'Rupee',
    'slammu': 'Raft',
    'shields': 'Shield',
    'silver': 'Silvers',
    'recorder': 'Tooter',
    'jess': 'Tooter',
    'tringle': 'Triforce',
    'tri': 'Triforce',
    'banana': 'WoodenBoomer',
    'boomerang': 'WoodenBoomer',
    'sword': 'WoodSword',
    'swords': 'WoodSword',
}

# Multi-word aliases (checked before single-word)
HASH_ALIASES_MULTI = {
    'spice rack': 'Ladder',
    'any key': 'AnyKey',
    'blue candle': 'BlueCandle',
    'blue potion': 'BluePotion',
    'blue ring': 'BlueRing',
    'magic boomer': 'MagicBoomer',
    'old man': 'OldMan',
    'red candle': 'RedCandle',
    'red potion': 'RedPotion',
    'red ring': 'RedRing',
    'small heart': 'SmallHeart',
    'white sword': 'WhiteSword',
    'wooden boomer': 'WoodenBoomer',
    'wood sword': 'WoodSword',
    'magic key': 'AnyKey',
}

# Named seed presets for Z1R.
SEED_PRESETS = {
    'abns22_swiss':      'oIbnPfPb01HJEAN8LBIMmlBWz!gGeqNjpYphk',
    'abns22_bracket':    '143oNtDD4RQLqGPLpaSfjC2AnRmLOpqFAW5QT4',
    'abns22_top8':       '143oNtDD4RQLrJy5BfGRUWmqGpjvyjX29Xj97o',
    'consternation':     'oIbnPfPb01Hll3D29Bc2!etrojQOSjJQZUJ3A',
    '2019brackets':      'oIbnPfPaymqfyH4t7pgvRD4cP1H1I7sPTblX9',
    'rr2024':            'oIbq2JttN3ae9PzaCJOsVpXedaDD1C2B1yAt8',
    'sgl24':             'oIbnPfPb01HmodtgCWCvSuAbuHPqZRIVKCvKj',
    'bettypls':          'q9yBZxHDLh6lovXqLgSNrmEieda2tqJLXZUEo',
    'excavator':         'hvFx!4yyHzSQ4RIGBlC82EgnMP0hKMULyMo0WP',
    'jesscherk':         'oIbnPg!o7RsFZtKnEc9AAI8WI7bpnQVS16uNU',
    'magsrush':          'oIbnPfPb01Hll3F1P2W2uMZOcOJWr!jS05L0A',
    'babysfirsthdn':     'oVeI512duFh1JQJ6dol1rhfGazsxVWEBeazWF',
    'walkitin':          'oIbnPfPaymqfyH3OJhgiKHew0nr6Guj6lqHtv',
    'swordlessplus':     'oIbnPfPb01Hns9ilYf3aXrUwNv7SZU05a56UV',
    'randomforce':       'oIbnPfPazW!1troLaKovLCWdzL0Ech79PCP7x',
    'rr2025':            'CKnGZ6u7XaVW!hJ!sGTvkRim82t8PvIW1BEycZo',
    'sgl25online':       '12TDBJOu7zgjkBGTDwHViA9wS3IpdJcvhCVEtu9',
    'power':             '143oNtDD4PAvBt5G8xyCFu5kwp7tS8vUBVpiZY',
    'courage':           'oIbnQLMCpyScZbUVFbgpGKPLsHFflaoYKIxoA',
    'wisdom':            'oIbnPfPb0mR7ggY12zwI0QNIY620UnhU8kiC3',
    'sgl25ip':           '12TDBJOu7zgjkBGTDsoDP5Hg7jnJNmTUCh4wT9X',
    'ttp4rp':            '24hJoDaoq92qaumIfio4Qq8LtfU0Xt8tpG3Iafo',
    'ttp4hopla':         'oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW',
    'ttp4consternation': 'oIbnPfPb01Hll3D295IGDxxjR4UwfEok8P4MD',
    'afbns_swiss':       '1K9hKZCQamJvAprO0CLKqHgZk0MR1RqiE9bfe9xv',
    'afbns_bracket':     '12V4XiZA!b3mWgwigt9JQcwZSUlpadoHsJNny3J',
    'afbns_top8':        '12V4XiZA!b3mWh!GQFcZr9rUkzjsoaFlYFS64EU',
}

# Presets used by !ttp2 / !ttp3 / !ttp4 random pickers
TTP2_PRESETS = [
    'bettypls', 'excavator', 'jesscherk', 'magsrush',
    'babysfirsthdn', 'walkitin', 'swordlessplus',
]
TTP3_PRESETS = ['power', 'courage', 'wisdom']
TTP4_PRESETS = ['ttp4rp', 'ttp4hopla', 'ttp4consternation']
