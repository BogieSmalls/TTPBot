"""What a TTP tournament race room says about picks, and what it records.

Pure: no sockets, no clock. The handler reads the engine's `pickState` and the
tournament's frozen flagsets, asks this module what to say or write, writes,
then tells the room. Nothing here decides who owns a pick: that is the engine's
`pickState`, read as it is.

A game's flagset is recorded when the race starts, from the last roll still
standing. A cancel stands until a new roll, whichever bot rolled and whatever
race info still says, so a cancelled roll uses nothing up.
"""

import re
from collections import namedtuple

#: What the room says when it opens, whose pick it is, which commands that
#: racer may use, whether the Game 1 decision is still owed, and anything
#: Council must hear about (said in the room with `with_council`).
RoomPlan = namedtuple('RoomPlan', 'opening owner commands needs_decision notices')

#: What to say back to a command, and the engine write that must succeed
#: first. `op` is `(operation, payload)` or None. The text is said only after
#: the write is recorded: decide, write, then tell.
Reply = namedtuple('Reply', 'text op')

#: A roll checked against the game. `warning` goes to the room and Council;
#: `ask` goes to the room only. `code` names the warning on Council's series
#: card: 'wrong-roll', 'not-frozen' or 'registry-edit'.
RollCheck = namedtuple('RollCheck', 'ok flagset warning ask code', defaults=(None,))

NONE, ROLLED, CANCELLED = 'none', 'rolled', 'cancelled'

#: The bots' own words, as the room logs have them. SahasrahBot spells
#: "Reseting" that way.
ROLL_COMPLETE = 'Seed rolling complete.'
ROLL_CLEARED = ('Reseting bot state.', 'Seed cleared.')

RANDOM_OFF = "Random flagsets are off in tournament rooms. Each game's flagset is picked."
RACE_OFF = 'In tournament rooms, !race is off. Roll the picked flagset with !flags.'
PRESET_OFF = "In tournament rooms, presets don't roll. Roll the picked flagset with !flags."
CANCELLED_WARNING = 'That seed was cancelled, so this game needs a new roll before the race starts.'
NO_ROLL_AT_START = 'The race started with no roll standing, so no flagset was recorded for this game'
NOT_SAVED = "I couldn't confirm that with the bracket, so it wasn't saved. Please try again in a moment."
#: Said to the room and Council while nobody owns Game 2's pick. The room
#: names the picker once Council records the decision.
NO_PICKER = 'Nobody has recorded who picks Game 2, so this room names no picker until Council does'

_FLAGS = re.compile(r'Flags: (\S+)')
#: `[time] SahasrahBot: text`. A person's name carries `#1234`, so a person
#: called SahasrahBot never matches.
_LOG_LINE = re.compile(r'^(?:\[[^\]]*\]\s+)?(?:SahasrahBot|TTPBot): (.*)$')
_TAG = re.compile(r'^TTP[0-9]+\s+', re.IGNORECASE)
EN_DASH = '–'


# -- rolls ---------------------------------------------------------------

def current_roll(info_bot):
    """The last `Flags: F` in race info, or None.

    The last, not the first: a room's info can hold an older segment ahead of
    the roll that stands.
    """
    found = _FLAGS.findall(info_bot or '')
    return found[-1] if found else None


def _step(state, text):
    if ROLL_COMPLETE in text:
        return ROLLED
    if any(words in text for words in ROLL_CLEARED):
        return CANCELLED
    return state


def roll_state(bot_messages, start=NONE):
    """Walk chat messages oldest first. Only bot messages count; the last word wins."""
    state = start
    for message in bot_messages or ():
        if not isinstance(message, dict) or not message.get('is_bot'):
            continue
        state = _step(state, message.get('message_plain') or message.get('message') or '')
    return state


def roll_state_from_log(text):
    """The same walk over a room's public `/log`, from SahasrahBot and TTPBot lines only."""
    state = NONE
    for line in (text or '').splitlines():
        found = _LOG_LINE.match(line.strip())
        if found:
            state = _step(state, found.group(1))
    return state


def flags_at_start(info_bot, state):
    """The flags to record when the race starts, or None with no roll standing."""
    return current_roll(info_bot) if state == ROLLED else None


# -- reading pickState ---------------------------------------------------

def short_name(name):
    """'TTP5 Uphill Battle' is 'Uphill Battle' in a room."""
    return _TAG.sub('', name or '')


def _games(pick_state):
    return {int(n): g for n, g in (pick_state.get('games') or {}).items()}


def _chosen(pick_state):
    return {int(n): c for n, c in (pick_state.get('chosen') or {}).items()}


def _owners(pick_state):
    return {int(n): who for n, who in (pick_state.get('owners') or {}).items()}


def _played_before(pick_state, game):
    """Flagsets other games raced on. This game's own recording (a rematch) is not one."""
    return [g['flagset'] for n, g in sorted(_games(pick_state).items()) if n != game]


def _left(pick_state, game, flagsets):
    played = _played_before(pick_state, game)
    return [f['name'] for f in flagsets if f['name'] not in played]


def _game3(pick_state, flagsets):
    """Game 3's flagset when Games 1 and 2 are on record and one is left."""
    games = _games(pick_state)
    left = _left(pick_state, 3, flagsets)
    if 1 in games and 2 in games and len(left) == 1 and not pick_state.get('warnings'):
        return left[0]
    return None


def _rematch(pick_state, game, room):
    """The flagset this game was raced on in a retired room, when `room` is its rematch.

    Game 3 is the flagset left either way, so only Games 1 and 2 have one.
    A room recorded from itself has started; it is not a rematch.
    """
    if room is None or game == 3:
        return None
    recorded = _games(pick_state).get(game)
    if recorded and recorded.get('room') != room:
        return recorded['flagset']
    return None


def expected_flagset(pick_state, game, flagsets, room=None):
    """What this game should be raced on, if anyone knows yet.

    `room` is this room's URL, so a rematch room expects the flagset its game
    was raced on in the retired room.
    """
    chosen = _chosen(pick_state).get(game)
    if chosen:
        return chosen['flagset']
    if game == 3:
        return _game3(pick_state, flagsets)
    return _rematch(pick_state, game, room)


def _commands(names, flagsets):
    return ' '.join('!' + f['command'] for f in flagsets if f['name'] in names)


def _flags_of(name, flagsets):
    return next(f['flags'] for f in flagsets if f['name'] == name)


def with_council(text, told):
    """Say Council has been told only once that notice has gone through."""
    if told:
        return text + ', and Council has been told.'
    return text + ". I couldn't reach Council, so please tell them in Discord."


# -- what the room says --------------------------------------------------

def heading(pick_state, game, *, tag, round_label, score=None):
    """'TTP5 Round 1, Game 2: thomjay leads 1–0.'"""
    better, other = pick_state['betterSeed'], pick_state['otherSeed']
    prefix = '{} {}, Game {}: '.format(tag, round_label, game)
    score = score or {}
    a, b = score.get(better), score.get(other)
    if game == 1 or a is None or b is None or a + b != game - 1:
        return prefix + '{} vs {}.'.format(better, other)
    if a == b:
        return prefix + 'tied {}{}{}.'.format(a, EN_DASH, b)
    leader, high, low = (better, a, b) if a > b else (other, b, a)
    return prefix + '{} leads {}{}{}.'.format(leader, high, EN_DASH, low)


def _decided_line(first, second, flagsets, pick_state, ready=True):
    text = '{} picks Game 1 and {} picks Game 2.'.format(first, second)
    if ready:
        text += " {}, when you're ready: {}".format(first, _commands(_left(pick_state, 1, flagsets), flagsets))
    return text


def _roll_line(pick_state, game, name, flagsets, rematch=False):
    """'Game 2 is Muffle Rug. Roll it when everyone's ready: !flags F If there's a Game 3, it's Pick 5.'

    A rematch room says 'Game 2 is a rematch on Muffle Rug.'
    """
    text = "Game {} is {}{}. Roll it when everyone's ready: !flags {}".format(
        game, 'a rematch on ' if rematch else '', short_name(name), _flags_of(name, flagsets))
    if game == 2:
        rest = [n for n in _left(pick_state, 2, flagsets) if n != name]
        if len(rest) == 1:
            text += " If there's a Game 3, it's {}.".format(short_name(rest[0]))
    return text


def _is_rematch(pick_state, game, room):
    """A rematch room with nothing chosen for its game: it rolls what was raced."""
    return not _chosen(pick_state).get(game) and _rematch(pick_state, game, room) is not None


def plan_for_room(pick_state, game, *, flagsets, tag, round_label, score=None, room=None):
    """What the room says when it opens, from the engine's pickState.

    `room` is this room's URL, which tells a rematch room from one that started.
    """
    better, other = pick_state['betterSeed'], pick_state['otherSeed']
    top = heading(pick_state, game, tag=tag, round_label=round_label, score=score)
    decision = pick_state.get('decision')
    owners = _owners(pick_state)
    expected = expected_flagset(pick_state, game, flagsets, room=room)
    played = _played_before(pick_state, game)
    notices = []

    if game == 3:
        if expected and expected == _game3(pick_state, flagsets):
            line = "{} This is {}, the flagset left, so nobody picks. Roll it when everyone's ready: !flags {}".format(
                top, short_name(expected), _flags_of(expected, flagsets))
            return RoomPlan([line], None, [], False, [])
        if expected:
            line = "{} This is {}, as Council recorded, so nobody picks. Roll it when everyone's ready: !flags {}".format(
                top, short_name(expected), _flags_of(expected, flagsets))
            return RoomPlan([line], None, [], False, [])
        if pick_state.get('warnings'):
            notices.append('A flagset was played twice, so Council decides Game 3')
        else:
            notices.append("Games 1 and 2 aren't both on record, so Council decides Game 3's flagset")
        return RoomPlan([top], None, [], False, notices)

    if game == 2 and 1 not in _games(pick_state):
        # Review Focus 1: no played flagset is named until one is on record.
        notices.append("Game 1's flagset isn't on record, so this room can't rule one out")

    if expected:
        line = _roll_line(pick_state, game, expected, flagsets, rematch=_is_rematch(pick_state, game, room))
        return RoomPlan([top, line], owners.get(game), [], False, notices)

    if game == 1 and not decision:
        line = ("{}, you're the better seed, so this decision is yours: !pick to choose this game's flagset, "
                'or !defer to give it to {} and take Game 2.').format(better, other)
        return RoomPlan([top, line], better, ['pick', 'defer'], True, notices)

    owner = owners.get(game)
    if not owner:
        # Codex requirement 2: a roll can't show who owned the Game 1 decision.
        notices.append(NO_PICKER)
        return RoomPlan([top], None, [], False, notices)

    left = _left(pick_state, game, flagsets)
    commands = [f['command'] for f in flagsets if f['name'] in left]
    if game == 1:
        line = _decided_line(owners[1], owners[2], flagsets, pick_state)
    else:
        line = '{} owns this pick.'.format(owner)
        for name in played:
            line += ' {} has been played.'.format(short_name(name))
        line += " When you're ready: {}".format(_commands(left, flagsets))
    return RoomPlan([top, line], owner, commands, False, notices)


def reply_to_command(pick_state, game, speaker, command, *, flagsets, rolled=False, room=None):
    """The answer to !pick, !defer or a flagset command, and the write it needs.

    `room` is this room's URL: a rematch room answers with its rematch roll,
    and a room that has itself started answers as it always has.
    """
    better, other = pick_state['betterSeed'], pick_state['otherSeed']
    match_id = pick_state['matchId']
    games = _games(pick_state)
    command = (command or '').lower()
    rematch = _rematch(pick_state, game, room)
    started_here = game in games and rematch is None

    if command in ('pick', 'defer'):
        if speaker != better:
            return Reply('Only {}, the better seed, decides who picks first.'.format(better), None)
        if game != 1 or 1 in games:
            if pick_state.get('decision') and not started_here:
                return Reply('That decision is already recorded.', None)
            return Reply('Game 1 has started, so Council records that decision now.', None)
        decision = pick_state.get('decision')
        if decision:
            if decision.get('choice') == command:
                owners = _owners(pick_state)
                return Reply("That's already recorded: {} picks Game 1 and {} picks Game 2.".format(
                    owners[1], owners[2]), None)
            return Reply('That decision is already recorded. Council can change it.', None)
        first, second = (better, other) if command == 'pick' else (other, better)
        ready = not rolled and not _chosen(pick_state).get(1)
        text = _decided_line(first, second, flagsets, pick_state, ready=ready)
        return Reply(text, ('decidePick', {'matchId': match_id, 'choice': command,
                                           'actor': speaker, 'by': 'racer'}))

    flagset = next((f for f in flagsets if f['command'].lower() == command), None)
    if flagset is None:
        return Reply(None, None)
    if game == 3:
        name = expected_flagset(pick_state, 3, flagsets)
        if name:
            return Reply('Game 3 is {}, the flagset left, so nobody picks.'.format(short_name(name)), None)
        return Reply("Nobody picks Game 3. Council decides its flagset.", None)
    if rematch is not None:
        name = expected_flagset(pick_state, game, flagsets, room=room)
        return Reply(_roll_line(pick_state, game, name, flagsets, rematch=_is_rematch(pick_state, game, room)), None)
    if game in games:
        return Reply('Game {} has started, so its flagset is the one it was raced on.'.format(game), None)
    owner = _owners(pick_state).get(game)
    if not owner:
        if game == 1:
            return Reply('{} decides first: !pick or !defer.'.format(better), None)
        return Reply('Nobody has recorded who picks Game 2 yet. Council can record it.', None)
    if speaker != owner:
        return Reply("This one is {}'s pick.".format(owner), None)
    if flagset['name'] in _played_before(pick_state, game):
        return Reply("{} has been played. When you're ready: {}".format(
            short_name(flagset['name']), _commands(_left(pick_state, game, flagsets), flagsets)), None)
    return Reply(_roll_line(pick_state, game, flagset['name'], flagsets),
                 ('choosePick', {'matchId': match_id, 'game': game, 'flagset': flagset['name'],
                                 'actor': speaker, 'by': 'racer'}))


def check_roll(flags, pick_state, game, *, flagsets, state=ROLLED, registry_name=None, room=None):
    """Whether a roll fits this game. Never blocks it: SahasrahBot can't be stopped.

    Only a string on the frozen list counts. `registry_name` is the Flag
    Registry's name for the string, so a newer version of a frozen flagset is
    named as such (Review Focus 5) rather than called unknown. `room` is this
    room's URL, so a rematch room expects its game's raced flagset.
    """
    if state == CANCELLED:
        return RollCheck(False, None, CANCELLED_WARNING, None)
    if state != ROLLED or not flags:
        return RollCheck(False, None, None, None)
    expected = expected_flagset(pick_state, game, flagsets, room=room)
    should = ' This game should be {}.'.format(short_name(expected)) if expected else ''
    ask = None
    if game == 1 and not pick_state.get('decision') and 1 not in _games(pick_state):
        ask = ('{}, this seed was rolled before a !pick or !defer. Please confirm with !pick or !defer, '
               'so Game 2 has a picker.').format(pick_state['betterSeed'])
    found = next((f for f in flagsets if f['flags'] == flags), None)
    if found is None:
        frozen = {short_name(f['name']).lower(): f['name'] for f in flagsets}
        newer = frozen.get(short_name(registry_name or '').lower())
        if newer:
            warning = ("That seed is a different {} string from the one frozen for this tournament, "
                       "so it won't count until Council confirms the change.{} !cancel clears it").format(
                           short_name(newer), should)
            return RollCheck(False, None, warning, ask, 'registry-edit')
        warning = "That seed isn't one of this tournament's flagsets, so it won't count.{} !cancel clears it".format(should)
        return RollCheck(False, None, warning, ask, 'not-frozen')
    name = found['name']
    if name in _played_before(pick_state, game):
        warning = 'That seed is {}, which was already played.{} !cancel clears it'.format(short_name(name), should)
        return RollCheck(False, name, warning, ask, 'wrong-roll')
    if expected and name != expected:
        warning = 'That seed is {}, but this game should be {}. !cancel clears it'.format(
            short_name(name), short_name(expected))
        return RollCheck(False, name, warning, ask, 'wrong-roll')
    return RollCheck(True, name, None, ask)


def reminder(pick_state, game, minutes, *, flagsets, rolled=False):
    """The 15 and 10 minute reminders, while a pick is owed. Never chooses for anyone."""
    if game == 3 or game in _games(pick_state):
        return None
    if game == 1 and not pick_state.get('decision'):
        return '{}, the race starts in {} minutes. Decide with !pick or !defer.'.format(
            pick_state['betterSeed'], minutes)
    owner = _owners(pick_state).get(game)
    if not owner or rolled or _chosen(pick_state).get(game):
        return None
    return "{}, the race starts in {} minutes and this game's pick is still yours: {}".format(
        owner, minutes, _commands(_left(pick_state, game, flagsets), flagsets))
