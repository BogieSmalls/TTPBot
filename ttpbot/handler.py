import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

import aiohttp
from racetime_bot import RaceHandler

#: League results waiting out their settle. Held so a pending one is not
#: collected before it files.
_LEAGUE_RESULTS = set()

#: How a tournament room names itself in the invite log line.
TOURNAMENT_LABELS = {'autumn': 'Autumn', 'corto': 'Torneo Corto', 'ttp': 'TTP tournament'}

from .flag_summary import FlagStringError, decode as decode_flags, format_summary
from .flagset_registry import reduce as reduce_word, registry
from .grace import GRACE_START, STARTED, GraceRace, entrants_from
from .matchup import matchup_reply

from .config import (
    HASH_ALIASES,
    HASH_ALIASES_MULTI,
    LEAGUE_ROOM_INFO_PREFIX,
    LEAGUE_WEEKS,
    PRESET_ALIASES,
    PRESET_NAMES,
    REMINDER_SCHEDULE,
    TTP2_PRESETS,
    TTP3_PRESETS,
    TTP4_PRESETS,
    TTP5_PRESETS,
    TIMEZONE,
    Z1RR_DISCORD_URL,
)
from .paths import ensure_parent_dir, runtime_path
from .room_policy import (
    room_identity, is_corto_room, is_autumn_room, is_league_room, is_ttp_scheduled_room,
    is_ttp_tournament_room,
)
from .schedule import find_nearest_scheduled_race, get_todays_remaining_races
from .season_record import flags_text, info_text, seasons, welcome_text
from . import tournament_room as picks
from .autumn.engine import NOT_RECORDED

#: How often a TTP tournament room looks at the clock for its opening and reminders.
TTP_TICK_SECONDS = 15
#: Reminders before the race, in minutes, while a pick is owed.
TTP_REMINDERS = (15, 10)
#: A reminder more than this late (a restart) is skipped rather than sent.
TTP_REMINDER_GRACE_MINUTES = 2
ENGINE_DOWN = "I can't reach the bracket right now, so picks here can't be saved"
NO_FLAGSETS = "This tournament has no frozen flagsets yet, so picks can't run here"

# Real flag strings are 30 or more characters. Vanilla, the one short exception, is registered.
MIN_FLAG_STRING_LENGTH = 19
PRESET_LIST = 'See https://z1rracing.com/flagsets/ for the list.'
CHAT_LOG_DIR = runtime_path('chat_logs')
LEARNED_ALIASES_FILE = runtime_path('learned_aliases.json')
RECENT_ROOM_HISTORY_WINDOW = timedelta(seconds=90)
GENERIC_WELCOME_MESSAGE = (
    "Hi, I'm TTPBot. I can help with seed rolling, hash confirmation, "
    "and Z1RR links. Type !help to see available commands."
)

# Merged alias dict built once at import, extended by learned aliases
_all_aliases = dict(HASH_ALIASES)


def _load_learned_aliases():
    """Load learned aliases from disk and merge into the alias dict."""
    try:
        with open(LEARNED_ALIASES_FILE, 'r') as f:
            learned = json.load(f)
            _all_aliases.update(learned)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_learned_alias(typo, canonical):
    """Persist a newly learned alias to disk."""
    try:
        try:
            with open(LEARNED_ALIASES_FILE, 'r') as f:
                learned = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            learned = {}
        learned[typo] = canonical
        ensure_parent_dir(LEARNED_ALIASES_FILE)
        with open(LEARNED_ALIASES_FILE, 'w') as f:
            json.dump(learned, f, indent=2)
        _all_aliases[typo] = canonical
    except Exception:
        pass


# Load learned aliases on import
_load_learned_aliases()



def _fuzzy_match(word):
    """Try to fuzzy-match a word against all known aliases. Returns (canonical, matched_key) or None."""
    candidates = list(_all_aliases.keys())
    matches = get_close_matches(word, candidates, n=1, cutoff=0.75)
    if matches:
        return _all_aliases[matches[0]], matches[0]
    return None


def parse_hash(text):
    """
    Try to parse a chat message as a 4-item ROM hash.

    Handles single-word and multi-word aliases (e.g. "spice rack").
    If 3/4 match exactly, attempts fuzzy matching on the unknown word.
    Returns (items, fuzzy_word) where items is a list of 4 canonical names
    or None. fuzzy_word is the typo that was fuzzy-matched (or None).
    """
    # Strip commas, periods, and other punctuation players might use
    cleaned = text.lower().replace(',', ' ').replace('.', ' ').replace(';', ' ')
    words = cleaned.split()
    items = []
    skipped = []  # (index, word) for unmatched words
    i = 0
    while i < len(words):
        # Try two-word match first
        if i + 1 < len(words):
            two_word = words[i] + ' ' + words[i + 1]
            if two_word in HASH_ALIASES_MULTI:
                items.append(HASH_ALIASES_MULTI[two_word])
                i += 2
                continue
        # Try single-word match
        if words[i] in _all_aliases:
            items.append(_all_aliases[words[i]])
            i += 1
            continue
        # Unknown word -- track it
        skipped.append((len(items), words[i]))
        i += 1

    if len(items) == 4 and len(skipped) == 0:
        return items, None, None

    # Fuzzy matching: exactly 3 matched + 1 unknown word
    if len(items) == 3 and len(skipped) == 1:
        insert_pos, unknown_word = skipped[0]
        result = _fuzzy_match(unknown_word)
        if result:
            canonical, matched_key = result
            items.insert(insert_pos, canonical)
            return items, unknown_word, canonical

    return None, None, None


class TTPRaceHandler(RaceHandler):
    """
    Handler for Z1RR rooms.

    Handles commands, hash confirmation, and chat logging in all watched rooms.
    TTP scheduled rooms also get the TTP welcome and timed reminders.
    """

    #: Set by TTPBot at start-up. racetime_bot constructs handlers itself and
    #: gives them no reference back to the bot, so shared state arrives here.
    grace_ledger = None
    tc_preferences = None
    grace_enforced = False

    stop_at = ['cancelled', 'finished']

    #: Set by TTPBot at start-up, as the grace ledger is. None when League
    #: result recording is switched off.
    results_recorder = None
    autumn_results = None
    corto_results = None
    ttp_results = None

    #: Set by TTPBot when the ttp runner is built, as the collectors are: the
    #: bracket engine, the Live Ops notifier (an async callable returning True
    #: only when Discord took the post), and the room name -> URL resolver.
    ttp_engine = None
    ttp_live_ops = None
    ttp_room_url = None

    #: Class defaults, so a handler built without __init__ still reads them.
    ttp_tournament_room = False
    roll_state = picks.NONE
    _ttp_history_seen = False
    _ttp_started = False
    _ttp_checked = None
    #: Race info as it stood at the last cancel (or with no roll standing).
    #: A roll is checked only once race info has moved on from it.
    _ttp_stale_info = None
    _ttp_flagsets = None
    _ttp_task = None
    _ttp_lock = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reminders_sent = set()
        self.scheduled_time = None
        self.bot_created = False
        self.ttp_scheduled_room = False
        self.league_room = False
        self.autumn_room = False
        self.corto_room = False
        self.ttp_tournament_room = False
        self.reminder_task = None
        self.pending_hash = None
        self.pending_hash_user = None
        self.ready_entrants = set()
        self.recap_data = {
            'hash_items': None,
            'hash_proposer': None,
            'hash_confirmer': None,
            'hash_confirm_method': None,
            'self_confirm_attempts': [],
            'pbs': [],
        }
        #: Grace minutes for this room, set up once the start time is known.
        self.grace = None
        self.grace_task = None
        self.sahasrahbot_present = False
        #: Set by !ttpbot: SahasrahBot is in the room but not answering, so
        #: TTPBot rolls seeds here and stops deferring to it.
        self.sahasrahbot_overridden = False
        self.seed_rolled = False
        self.history_command_cutoff_utc = None

    @staticmethod
    def _is_sahasrahbot_msg(message):
        """Return True if this is a bot message from SahasrahBot.

        Bot messages on racetime.gg have user=null and the bot name in
        the 'bot' field (a plain string).
        """
        if not message.get('is_bot'):
            return False
        bot_name = message.get('bot') or ''
        return 'sahasrahbot' in bot_name.lower()

    def _room_is_open(self):
        """Whether the race has yet to start.

        A reconnect to a race already under way (a restart or deploy mid-race)
        must say nothing: no welcome, no reminders, no invites. Racers are
        playing, and anything TTPBot posts then is noise.
        """
        status = (self.data.get('status') or {}).get('value')
        return status not in ('pending', 'in_progress', 'finished', 'cancelled')

    async def begin(self):
        self.ttp_scheduled_room = is_ttp_scheduled_room(self.data)
        self.league_room = is_league_room(self.data)
        self.autumn_room = is_autumn_room(self.data)
        self.corto_room = is_corto_room(self.data)
        self.ttp_tournament_room = is_ttp_tournament_room(self.data)
        self.history_command_cutoff_utc = self._recent_room_history_cutoff()

        if self.ttp_scheduled_room:
            self._determine_scheduled_time()

            # Detect if this room was created by the bot (has "Scheduled:" in info)
            info_bot = self.data.get('info_bot', '') or ''
            self.bot_created = 'Scheduled:' in info_bot

            if self.scheduled_time:
                now = datetime.now(TIMEZONE)
                minutes_until = (self.scheduled_time - now).total_seconds() / 60

                if minutes_until >= -1 and self._room_is_open():
                    # Race time is upcoming or just arrived - send reminders.
                    # Pre-mark reminders whose window is well past (>2 min ago)
                    # so a service restart doesn't dump all reminders at once.
                    for minutes_before, _ in REMINDER_SCHEDULE:
                        if minutes_until < minutes_before - 2:
                            self.reminders_sent.add(minutes_before)

                    self.reminder_task = asyncio.ensure_future(self._reminder_loop())

                if self.grace_ledger is not None:
                    self.grace = GraceRace(
                        self.scheduled_time, self.grace_ledger,
                        enforce=self.grace_enforced)
                    self.grace_task = asyncio.ensure_future(self._grace_loop())
                # If past the start time: skip reminders but still welcome.
        else:
            self.scheduled_time = None
            self.bot_created = False
            if not self._room_is_open():
                pass
            elif self.league_room:
                await self._send_league_invites()
            elif self._tournament_event():
                # Shared state is reseeded after reconnects and process restarts.
                self.state['_autumn_send_invites'] = self._send_autumn_invites
                await self._send_autumn_invites()
                if self.ttp_tournament_room:
                    # Opening messages and reminders. Each tick waits for chat
                    # history, so a restart never repeats what the room has.
                    self._ttp_task = asyncio.ensure_future(self._ttp_loop())

        # Request chat history to detect prior seed rolls and, for TTP rooms,
        # avoid duplicate welcomes/reminders.
        await self.ws.send(json.dumps({'action': 'gethistory'}))

    def _league_invite_ids(self):
        """Return the racetime ids to invite in this League room.

        Two for a 1v1, four for a co-op match (away team first).

        Prefers state seeded by the scheduler at room creation. After a
        restart that state is gone, so fall back to the room title, which
        this automation wrote itself to both info_user and info_bot. Another
        authorised category bot (e.g. SahasrahBot rolling a seed) can
        overwrite info_bot, so info_user is preferred and info_bot is the
        fallback.
        """
        seeded = (self.state or {}).get('league_race') or {}
        invite = seeded.get('invite')
        if (
            isinstance(invite, list)
            and len(invite) in (2, 4)
            and all(isinstance(i, str) and i for i in invite)
            and len(set(invite)) == len(invite)
        ):
            return list(invite)

        info_user = self.data.get('info_user', '') or ''
        info_bot = self.data.get('info_bot', '') or ''
        title = next(
            (
                value for value in (info_user, info_bot)
                if value.startswith(LEAGUE_ROOM_INFO_PREFIX)
            ),
            None,
        )
        if title is None:
            self.logger.warning('[%s] League title is unparseable: %r',
                                self.data.get('name'), info_bot)
            return []
        pairing = room_identity(title)[len(LEAGUE_ROOM_INFO_PREFIX):]
        # 'A vs. B' for a 1v1; 'A & C vs. B & D' for a co-op match, whose
        # title lists the away team first.
        sides = [side.split(' & ') for side in pairing.split(' vs. ')]
        if len(sides) != 2 or len(sides[0]) != len(sides[1]) or len(sides[0]) not in (1, 2):
            self.logger.warning('[%s] League title is unparseable: %r',
                                self.data.get('name'), title)
            return []
        names = sides[0] + sides[1]
        from .league.roster import RosterError, UnknownRacerError, load_roster
        try:
            roster = load_roster()
            return [roster.resolve(name).racetime_id for name in names]
        except (RosterError, UnknownRacerError) as exc:
            self.logger.warning('[%s] League invites unresolved: %s',
                                self.data.get('name'), exc)
            return []

    def _present_entrant_ids(self):
        """racetime ids already entered in this room."""
        entrants = self.data.get('entrants')
        if not isinstance(entrants, list):
            return set()
        present = set()
        for entrant in entrants:
            user = entrant.get('user') if isinstance(entrant, dict) else None
            user_id = (user or {}).get('id') if isinstance(user, dict) else None
            if isinstance(user_id, str) and user_id:
                present.add(user_id)
        return present

    def _autumn_invite_ids(self):
        """The racetime ids to invite to an Autumn room.

        Seeded state only, and no title fallback -- unlike the League's, which
        reads names out of the room title and resolves them against a committed
        roster. There is no committed roster for the tournament: the racetime ids
        live in the bracket engine, and reaching for them from here would put an
        HTTP call inside a websocket handler.

        It does not need one. The Autumn scheduler re-seeds this on every tick a
        race is inside its window, so a restart is covered by the next tick rather
        than by parsing a title -- which is a better recovery anyway, because the
        scheduler's list is the authoritative one.
        """
        seeded = (self.state or {}).get('autumn_race') or {}
        invite = seeded.get('invite')
        if (
            isinstance(invite, list)
            and len(invite) == 2
            and all(isinstance(i, str) and i for i in invite)
            and len(set(invite)) == len(invite)
        ):
            return list(invite)
        if invite:
            # Something was seeded and it is not two distinct ids. Said rather
            # than silently skipped: it means a racer has no racetime id on file.
            self.logger.warning(
                '[%s] Autumn invites are incomplete: %r',
                self.data.get('name'), invite)
        return []

    def _tournament_event(self):
        """The tournament runner's event this room belongs to, or None."""
        for event, flag in (('ttp', 'ttp_tournament_room'), ('corto', 'corto_room'), ('autumn', 'autumn_room')):
            if getattr(self, flag, False):
                return event
        return None

    def tournament_race(self):
        """What the runner seeded about a TTP tournament room's game, or None.

        `when` comes back as an aware datetime. None until the seed lands, which
        may be after `begin`: the runner re-seeds every tick it is in its window.
        """
        if not self.ttp_tournament_room:
            return None
        seeded = (self.state or {}).get('autumn_race') or {}
        if seeded.get('event') != 'ttp' or not isinstance(seeded.get('game'), int):
            return None
        try:
            when = datetime.fromisoformat(seeded['when'])
        except (KeyError, TypeError, ValueError):
            return None
        if when.tzinfo is None:
            return None
        return dict(seeded, when=when)

    # -- TTP tournament picks ---------------------------------------------
    #
    # The engine owns who picks and what was played; tournament_room says what
    # the room hears. Every write is decided, written, then told. Nothing here
    # may stop or delay the race: an engine that is down is reported, never
    # waited on.

    def _ttp_said(self):
        """TTPBot's own words in this room: from history, then as it speaks."""
        return self.state.setdefault('ttp_said', [])

    def _ttp_roll_said(self):
        """TTPBot's words since the last roll or cancel, so each roll is warned about once."""
        return self.state.setdefault('ttp_roll_said', [])

    async def _ttp_say(self, text):
        await self.send_message(text)
        self._ttp_said().append(text)
        self._ttp_roll_said().append(text)

    async def _ttp_say_once(self, text):
        if text and text not in self._ttp_said():
            await self._ttp_say(text)

    def _ttp_room(self):
        name = self.data.get('name') or ''
        if self.ttp_room_url is not None:
            try:
                return self.ttp_room_url(name)
            except Exception:
                self.logger.warning('[%s] room URL unresolved', name, exc_info=True)
        return name

    @staticmethod
    def _ttp_labels(race):
        tag = 'TTP{}'.format(race.get('edition') or '')
        label = race.get('round')
        if not label:
            found = re.match(r'^W(\d+)-\d+$', race.get('match_id') or '')
            label = 'Round {}'.format(found.group(1)) if found else race.get('match_id')
        return tag, label

    async def _ttp_notify(self, race, text):
        """Live Ops, which is how Council hears. True only when the post went through."""
        if self.ttp_live_ops is None:
            return False
        tag, _ = self._ttp_labels(race)
        try:
            return bool(await self.ttp_live_ops('{} {} Game {} ({}): {}'.format(
                tag, race.get('match_id'), race.get('game'), self._ttp_room(), text)))
        except Exception:
            self.logger.warning('[%s] Live Ops notice failed', self.data.get('name'), exc_info=True)
            return False

    async def _ttp_tell_council(self, race, base, per_roll=False):
        """The room and Live Ops, once each. The room says Council was told only if it was.

        Once per room, or with `per_roll` once per roll: a wrong roll rolled
        again after a cancel is a new problem.
        """
        heard = self._ttp_roll_said() if per_roll else self._ttp_said()
        if any(said.startswith(base) for said in heard):
            return
        told = await self._ttp_notify(race, base + '.')
        await self._ttp_say(picks.with_council(base, told))

    async def _ttp_notify_once(self, race, text):
        """Live Ops only, once per room."""
        sent = self.state.setdefault('ttp_told', [])
        if text in sent:
            return
        sent.append(text)
        await self._ttp_notify(race, text)

    async def _ttp_read(self, race, score=False):
        """pickState, the frozen flagsets and, for Games 2 and 3, the score."""
        engine = self.ttp_engine
        state = await engine.picks(race['match_id'], race.get('edition'))
        if not self._ttp_flagsets:
            self._ttp_flagsets = await engine.tournament_flagsets()
        return state, self._ttp_flagsets, (await self._ttp_score(race) if score else None)

    async def _ttp_score(self, race):
        try:
            drawn = await self.ttp_engine.draw()
        except Exception:
            self.logger.warning('[%s] series score unread', self.data.get('name'), exc_info=True)
            return None
        match = next((m for m in drawn.get('matches', []) if m.get('id') == race['match_id']), None)
        series = (match or {}).get('series') or {}
        if not match or 'a' not in series or 'b' not in series:
            return None
        return {match.get('a'): series['a'], match.get('b'): series['b']}

    def _ttp_lock_held(self):
        if self._ttp_lock is None:
            self._ttp_lock = asyncio.Lock()
        return self._ttp_lock

    def _ttp_rebuild_from_history(self, messages):
        ordered = sorted(
            messages,
            key=lambda msg: self._parse_history_timestamp(msg.get('posted_at'))
            or datetime.min.replace(tzinfo=timezone.utc))
        # With no roll in reach of the history, this is 'none': nothing is
        # recorded and the room warns, rather than guess.
        self.roll_state = picks.roll_state(ordered)
        self.seed_rolled = self.roll_state == picks.ROLLED
        self._ttp_stale_info = None if self.seed_rolled else self.data.get('info_bot')
        said = self._ttp_said()
        since_roll = []
        for msg in ordered:
            text = msg.get('message_plain') or msg.get('message') or ''
            if picks.roll_state([msg]) != picks.NONE:
                since_roll = []
            elif msg.get('is_bot') and msg.get('bot') == 'TTPBot':
                since_roll.append(text)
            if msg.get('is_bot') and msg.get('bot') == 'TTPBot' and text not in said:
                said.append(text)
        self.state['ttp_roll_said'] = since_roll

    async def _ttp_after_history(self):
        self._ttp_history_seen = True
        status = (self.data.get('status') or {}).get('value')
        try:
            if status == 'in_progress':
                # A reconnect mid-race records again, which the engine treats
                # as the no-op it is.
                await self._ttp_start()
                return
            await self._ttp_tick()
            await self._ttp_check_roll()
        except Exception:
            self.logger.error('Tournament room history check raised exception.', exc_info=True)

    async def _ttp_set_roll(self, state):
        before, self.roll_state = self.roll_state, state
        self.seed_rolled = state == picks.ROLLED
        if state == before:
            return
        # A new roll or a cancel: what was said about the last roll is behind it.
        self.state['ttp_roll_said'] = []
        if state != picks.ROLLED:
            # The info the next roll must replace before it is checked.
            self._ttp_stale_info = self.data.get('info_bot')
        if not self._ttp_history_seen or not self._room_is_open():
            return
        try:
            if state == picks.CANCELLED:
                await self._ttp_say(picks.CANCELLED_WARNING)
                self._ttp_checked = None
            else:
                await self._ttp_check_roll()
        except Exception:
            self.logger.error('Tournament roll check raised exception.', exc_info=True)

    async def _ttp_race_data(self):
        status = (self.data.get('status') or {}).get('value')
        if status == 'in_progress':
            if self._ttp_task and not self._ttp_task.done():
                self._ttp_task.cancel()
            if self._ttp_history_seen:
                await self._ttp_start()
            # Otherwise the history, when it lands, records the start.
            return
        if self._ttp_history_seen and self._room_is_open():
            await self._ttp_check_roll()

    async def _ttp_check_roll(self):
        """Check the roll standing against the game, once per roll."""
        if self.roll_state != picks.ROLLED or not self._room_is_open():
            return
        info = self.data.get('info_bot')
        if self._ttp_stale_info is not None:
            if info == self._ttp_stale_info:
                # TTPBot's own roll lands before racetime shows its race info:
                # what is there is still the roll before it.
                return
            self._ttp_stale_info = None
        flags = picks.current_roll(info)
        key = (self.roll_state, flags)
        if key == self._ttp_checked:
            return
        race = self.tournament_race()
        if race is None or self.ttp_engine is None:
            return
        self._ttp_checked = key
        try:
            state, flagsets, _ = await self._ttp_read(race)
            if flags and flagsets and flags not in [f['flags'] for f in flagsets]:
                # A confirmed edit reaches the tournament without a restart.
                self._ttp_flagsets = None
                state, flagsets, _ = await self._ttp_read(race)
        except Exception:
            self.logger.warning('[%s] engine unread for a roll check', self.data.get('name'), exc_info=True)
            await self._ttp_tell_council(race, ENGINE_DOWN)
            return
        check = picks.check_roll(flags, state, race['game'], flagsets=flagsets,
                                 registry_name=registry.name_of(flags) if flags else None)
        if check.warning:
            await self._ttp_tell_council(race, check.warning, per_roll=True)
        await self._ttp_say_once(check.ask)

    async def _ttp_start(self):
        """Record the game's flagset from the roll standing as the race starts."""
        if self._ttp_started:
            return
        self._ttp_started = True
        race = self.tournament_race()
        flags = picks.flags_at_start(self.data.get('info_bot'), self.roll_state)
        if race is None or self.ttp_engine is None:
            self.logger.warning('[%s] TTP race started before its match was known; '
                                'the finished room is checked instead', self.data.get('name'))
            return
        room = self._ttp_room()
        if flags is None:
            try:
                # A restart whose history no longer reaches the roll: the
                # engine may already hold this room's recording.
                state = await self.ttp_engine.picks(race['match_id'], race.get('edition'))
                if ((state.get('games') or {}).get(str(race['game'])) or {}).get('room') == room:
                    return
            except Exception:
                pass
            await self._ttp_tell_council(race, picks.NO_ROLL_AT_START)
            return
        written = await self.ttp_engine.record_flagset(dict(
            matchId=race['match_id'], game=race['game'], flags=flags, room=room,
            source='room-start', actor='TTPBot', edition=race.get('edition')))
        if written.ok:
            state = (written.answer or {}).get('picks') or {}
            if race['game'] == 1 and state and not state.get('decision'):
                await self._ttp_notify_once(
                    race, 'Game 1 started with no !pick or !defer, so Council needs to record who picks Game 2.')
            return
        self.logger.warning('[%s] flagset not recorded at the start: %s', self.data.get('name'),
                            written.detail or written.outcome)
        if written.outcome == NOT_RECORDED:
            await self._ttp_tell_council(
                race, "The race started on a roll the bracket didn't accept, so no flagset was recorded for this game")
        else:
            await self._ttp_tell_council(
                race, "I couldn't confirm this game's flagset with the bracket, so it's checked again when the race finishes")

    async def _ttp_loop(self):
        try:
            while self._room_is_open():
                await self._ttp_tick()
                await asyncio.sleep(TTP_TICK_SECONDS)
        except asyncio.CancelledError:
            pass
        except Exception:
            self.logger.error('Error in tournament room loop', exc_info=True)

    async def _ttp_tick(self, now=None):
        """The opening once the match is known, then the reminders. Idempotent."""
        if not self.ttp_tournament_room or not self._ttp_history_seen or not self._room_is_open():
            return
        race = self.tournament_race()
        if race is None or self.ttp_engine is None:
            return
        async with self._ttp_lock_held():
            try:
                await self._ttp_open(race)
                await self._ttp_remind(race, now or datetime.now(timezone.utc))
            except Exception:
                self.logger.error('Error in tournament room tick', exc_info=True)

    async def _ttp_open(self, race):
        key = '{}|{}'.format(race['match_id'], race['game'])
        if self.state.get('ttp_opened') == key:
            return
        try:
            state, flagsets, score = await self._ttp_read(race, score=race['game'] > 1)
        except Exception:
            self.logger.warning('[%s] engine unread for the opening', self.data.get('name'), exc_info=True)
            await self._ttp_tell_council(race, ENGINE_DOWN)
            return
        if not flagsets:
            await self._ttp_tell_council(race, NO_FLAGSETS)
            return
        tag, label = self._ttp_labels(race)
        plan = picks.plan_for_room(state, race['game'], flagsets=flagsets, tag=tag,
                                   round_label=label, score=score)
        if plan.opening[0] not in self._ttp_said():
            for line in plan.opening:
                await self._ttp_say(line)
        for notice in plan.notices:
            await self._ttp_tell_council(race, notice)
        self.state['ttp_opened'] = key

    async def _ttp_remind(self, race, now):
        sent = self.state.setdefault('ttp_reminded', [])
        minutes_until = (race['when'] - now).total_seconds() / 60
        for minutes in TTP_REMINDERS:
            if minutes in sent or minutes_until > minutes:
                continue
            sent.append(minutes)
            if minutes_until < minutes - TTP_REMINDER_GRACE_MINUTES:
                continue
            state, flagsets, _ = await self._ttp_read(race)
            await self._ttp_say_once(picks.reminder(
                state, race['game'], minutes, flagsets=flagsets, rolled=self.roll_state == picks.ROLLED))

    def _ttp_season_commands(self, edition):
        """The season's random command and flagset commands, from its record."""
        try:
            number = int(str(edition))
        except (TypeError, ValueError):
            number = None
        if number is not None:
            season = next((s for s in seasons.oldest_first() if s.get('number') == number), None) or {}
        else:
            # Before the runner's seed lands: the season whose label the room carries.
            titles = [self.data.get(field) or '' for field in ('info_user', 'info_bot')]
            season = next((s for s in seasons.oldest_first()
                           if (s.get('tournament') or {}).get('roomLabel')
                           and any(t.startswith(s['tournament']['roomLabel'] + ' — ') for t in titles)), None) or {}
        commands = {f['command'].lower() for f in season.get('flagsets') or []}
        commands |= {f['command'].lower() for f in self._ttp_flagsets or []}
        return (season.get('randomCommand') or '').lower() or None, commands

    def _ttp_speaker(self, message, race, state):
        """Which racer said this, by racetime id when the runner knows both."""
        user = message.get('user') or {}
        runners = race.get('runners') or []
        invite = race.get('invite') or []
        if len(invite) == 2 and len(runners) == 2:
            who = next((name for rid, name in zip(invite, runners) if user.get('id') == rid), None)
        else:
            wanted = (user.get('name') or '').lower()
            who = next((name for name in runners if name.lower() == wanted), None) if wanted else None
        if who is None:
            return None
        return next((n for n in (state['betterSeed'], state['otherSeed']) if n.lower() == who.lower()), who)

    async def _ttp_command(self, command, message):
        """!pick, !defer and the season's flagset commands. True when answered here."""
        command = command.lower()
        race = self.tournament_race()
        edition = race.get('edition') if race else None
        random_command, flagset_commands = self._ttp_season_commands(edition)
        if command == 'race':
            await self.send_message(picks.RACE_OFF)
            return True
        if random_command and command == random_command:
            await self.send_message(picks.RANDOM_OFF)
            return True
        if command not in ('pick', 'defer') and command not in flagset_commands:
            return False
        if race is None or self.ttp_engine is None:
            await self.send_message("I don't know this room's match yet, so picks can't be saved. Please try again in a moment.")
            return True
        try:
            state, flagsets, _ = await self._ttp_read(race)
        except Exception:
            self.logger.warning('[%s] engine unread for a pick', self.data.get('name'), exc_info=True)
            await self.send_message(picks.NOT_SAVED)
            await self._ttp_tell_council(race, ENGINE_DOWN)
            return True
        reply = picks.reply_to_command(state, race['game'], self._ttp_speaker(message, race, state), command,
                                       flagsets=flagsets, rolled=self.roll_state == picks.ROLLED)
        if reply.text is None:
            return False
        if reply.op is None:
            await self.send_message(reply.text)
            return True
        operation, payload = reply.op
        write = {'decidePick': self.ttp_engine.decide_pick, 'choosePick': self.ttp_engine.choose_pick}[operation]
        written = await write(dict(payload, edition=race.get('edition')))
        if written.ok:
            await self._ttp_say(reply.text)
            if operation == 'choosePick':
                # A roll already standing is checked against the new choice.
                self._ttp_checked = None
                await self._ttp_check_roll()
            return True
        if written.outcome == NOT_RECORDED:
            try:
                reason = json.loads(written.detail or '{}').get('error')
            except (ValueError, AttributeError):
                reason = None
            await self.send_message("The bracket didn't take that: {}".format(reason or 'no reason given'))
        else:
            await self.send_message(picks.NOT_SAVED)
            await self._ttp_notify_once(race, "TTPBot couldn't confirm a pick with the bracket.")
        return True

    async def _send_autumn_invites(self):
        """Invite a tournament room's two racers, exactly once.

        The runner calls this on every tick the race is in its window, which
        is also when a TTP room learns its match: so its opening runs here too.
        """
        try:
            await self._send_invites(
                TOURNAMENT_LABELS[self._tournament_event()], 'autumn_invited', self._autumn_invite_ids())
        finally:
            if self.ttp_tournament_room:
                await self._ttp_tick()

    async def _send_league_invites(self):
        """Invite the scheduled racers exactly once."""
        await self._send_invites(
            'League', 'league_invited', self._league_invite_ids())

    async def _send_invites(self, label, guard_key, invite_ids):
        """Invite a list of racetime ids once, and never twice.

        The once-only guard lives in self.state, not an instance attribute:
        racetime_bot discards this handler when its websocket task ends and
        builds a new one (with the same self.state dict) for refresh_races
        reconnects, so an instance attribute would forget the invite and
        re-invite racers who are already entrants. self.state is lost on a
        process restart, which is intentional -- for the League the title-based
        fallback in _league_invite_ids() recovers invites, and for the tournament
        the scheduler re-seeds them on the next tick.
        """
        state = self.state if isinstance(self.state, dict) else None
        if state is not None and state.get(guard_key):
            return
        if not invite_ids:
            return
        # Only those not already in the room. invite_user() just writes to the
        # socket and never learns whether racetime accepted it, so a duplicate
        # invitation is not something we would find out about - and a retry
        # after a half-sent batch, or after a restart, would otherwise re-send
        # for a racer who is already an entrant.
        present = self._present_entrant_ids()
        invite_ids = [i for i in invite_ids if i not in present]
        if not invite_ids:
            if state is not None:
                state[guard_key] = True
            return
        # Set before awaiting so a concurrent begin() cannot double-invite.
        if state is not None:
            state[guard_key] = True
        try:
            for racetime_id in invite_ids:
                await self.invite_user(racetime_id)
        except Exception:
            # The guard is claimed before the sends, so a websocket that dies
            # midway would otherwise leave it set with nobody invited, and
            # every rebuilt handler would skip. Release it and let the next
            # handler try; re-inviting an existing entrant is harmless, being
            # stranded is not.
            if state is not None:
                state[guard_key] = False
            self.logger.warning(
                '[%s] %s invites failed; released for retry',
                self.data.get('name'), label, exc_info=True,
            )
            raise
        self.logger.info('[%s] invited %d %s racers',
                         self.data.get('name'), len(invite_ids), label)

    async def error(self, data):
        """A refused invitation is a room action failure, not a bot failure."""
        errors = data.get('errors')
        if (isinstance(errors, list) and errors
                and all(isinstance(message, str) and re.fullmatch(
                    r'.+ is not allowed to join this race\.', message)
                        for message in errors)):
            # racetime replies asynchronously, after invite_user has returned.
            # Keep the invite guard claimed: repeating a refusal on reconnect
            # used to terminate the handler, then the entire scheduler loop.
            self.logger.warning('[%s] Invitation refused by racetime: %s',
                                self.data.get('name'), '; '.join(errors))
            return
        await super().error(data)

    async def chat_history(self, data):
        """Check chat history for existing bot messages to avoid duplicates."""
        messages = data.get('messages', [])

        # Detect SahasrahBot presence from chat history
        for msg in messages:
            if self._is_sahasrahbot_msg(msg):
                self.sahasrahbot_present = True
                self.logger.info(
                    '[%s] SahasrahBot detected in chat history - seed commands deferred',
                    self.data.get('name'),
                )
                break

        # A !ttpbot survives a reconnect: SahasrahBot is still in the history.
        for msg in messages:
            if not msg.get('is_bot') and self._is_ttpbot_command(msg):
                self.sahasrahbot_overridden = True
                self.sahasrahbot_present = False
                self.logger.info(
                    '[%s] !ttpbot found in chat history - rolling seeds for SahasrahBot',
                    self.data.get('name'),
                )
                break

        if self.ttp_tournament_room:
            # A cancel since the roll counts, whichever bot rolled, so rolling
            # follows the last bot word rather than any old "complete".
            self._ttp_rebuild_from_history(messages)

        # Also detect if a seed was already rolled (avoids double-roll on restart).
        # SahasrahBot's rolls count too, or a !ttpbot could roll a second
        # seed. A third bot sending "Seed rolling complete." would lock rolling.
        for msg in ([] if self.ttp_tournament_room else messages):
            if msg.get('is_bot'):
                text = msg.get('message_plain', '') or ''
                if 'Seed rolling complete.' in text:
                    self.seed_rolled = True
                    self.logger.info(
                        '[%s] Seed already rolled before reconnect — locking',
                        self.data.get('name'),
                    )
                    break

        bot_messages = [
            msg.get('message_plain') or ''
            for msg in messages
            if msg.get('is_bot')
        ]

        # Check if we already welcomed this room
        already_welcomed = any(
            re.search(r'Welcome to TTP Season [0-9]+!', text)
            or any(f'Welcome to {goal}!' in text for goal in seasons.goals())
            or 'Welcome to Triforce Triple Play!' in text
            or GENERIC_WELCOME_MESSAGE in text
            for text in bot_messages
        )

        # Check which reminders were already sent
        for minutes_before, reminder_text in REMINDER_SCHEDULE:
            if any(reminder_text in text for text in bot_messages):
                self.reminders_sent.add(minutes_before)

        if already_welcomed or not self._room_is_open():
            # A long race can push the welcome out of the history window, and
            # a race under way is no place to say hello again.
            self.state['welcomed'] = True
        elif not self.state.get('welcomed'):
            if self.ttp_scheduled_room:
                await self.send_message(welcome_text(seasons.current(datetime.now(TIMEZONE))))
            else:
                await self.send_message(GENERIC_WELCOME_MESSAGE)
            self.state['welcomed'] = True

        await self._handle_recent_history_commands(messages)

        if self.ttp_tournament_room:
            await self._ttp_after_history()

    def _parse_history_timestamp(self, raw_timestamp):
        if not raw_timestamp:
            return None
        try:
            parsed = datetime.fromisoformat(
                raw_timestamp.replace('Z', '+00:00')
            )
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _latest_own_bot_message_timestamp(self, messages):
        timestamps = []
        for message in messages:
            if not message.get('is_bot'):
                continue
            if message.get('bot') != 'TTPBot':
                continue
            posted_at = self._parse_history_timestamp(message.get('posted_at'))
            if posted_at:
                timestamps.append(posted_at)
        return max(timestamps) if timestamps else None

    def _recent_room_history_cutoff(self):
        opened_at = self._parse_history_timestamp(self.data.get('opened_at'))
        if not opened_at:
            return None

        now = datetime.now(timezone.utc)
        if now - opened_at <= RECENT_ROOM_HISTORY_WINDOW:
            return opened_at
        return None

    async def _handle_recent_history_commands(self, messages):
        cutoff = getattr(self, 'history_command_cutoff_utc', None)
        if not cutoff:
            return
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        else:
            cutoff = cutoff.astimezone(timezone.utc)

        latest_bot_message = self._latest_own_bot_message_timestamp(messages)
        if latest_bot_message and latest_bot_message > cutoff:
            cutoff = latest_bot_message

        sorted_messages = sorted(
            messages,
            key=lambda msg: self._parse_history_timestamp(msg.get('posted_at'))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        for message in sorted_messages:
            if message.get('is_bot') or message.get('is_system'):
                continue
            posted_at = self._parse_history_timestamp(message.get('posted_at'))
            if not posted_at or posted_at < cutoff:
                continue
            text = (message.get('message') or message.get('message_plain') or '').strip()
            words = text.lower().split()
            if not words or not words[0].startswith(self.command_prefix.lower()):
                continue
            await self.chat_message({'message': message})
        self.history_command_cutoff_utc = None

    def _determine_scheduled_time(self):
        """Determine the scheduled start time for this race room."""
        if 'scheduled_time' in self.state:
            self.scheduled_time = datetime.fromisoformat(
                self.state['scheduled_time']
            )
            return

        now = datetime.now(TIMEZONE)
        self.scheduled_time = find_nearest_scheduled_race(now)
        if self.scheduled_time:
            self.state['scheduled_time'] = self.scheduled_time.isoformat()

    async def _reminder_loop(self):
        """Send reminders at configured intervals before the scheduled start."""
        try:
            while True:
                now = datetime.now(TIMEZONE)
                seconds_until = (self.scheduled_time - now).total_seconds()
                minutes_until = seconds_until / 60

                for minutes_before, message in REMINDER_SCHEDULE:
                    if minutes_before not in self.reminders_sent:
                        if minutes_until <= minutes_before:
                            await self.send_message(message)
                            self.reminders_sent.add(minutes_before)

                # All reminders sent and past start time
                if len(self.reminders_sent) >= len(REMINDER_SCHEDULE):
                    return

                await asyncio.sleep(15)
        except asyncio.CancelledError:
            pass
        except Exception:
            self.logger.error('Error in reminder loop', exc_info=True)

    async def _grace_loop(self):
        """Run the grace countdown until it settles or the race starts.

        Separate from the reminder loop: reminders stop at the scheduled time,
        which is exactly when this begins to matter.
        """
        try:
            while True:
                status = (self.data.get('status') or {}).get('value')
                if status not in ('open', 'invitational'):
                    if status in ('pending', 'in_progress'):
                        self._credit_early_start()
                    return
                decision = self.grace.tick(datetime.now(TIMEZONE), entrants_from(
                    self.data,
                    monitors=self.data.get('monitors'),
                    opened_by=self.data.get('opened_by'),
                ))
                self.grace_ledger.apply(decision, entrants_from(self.data))
                for line in decision.messages:
                    await self.send_message(line)
                if decision.blocked:
                    self.logger.info('Grace: no force start in %s (%s)',
                                     self.data.get('name'), decision.blocked)
                if decision.force_start:
                    self.logger.info('Grace: force starting %s', self.data.get('name'))
                    await self.force_start()
                if self.grace.finished:
                    return
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            pass
        except Exception:
            # A race that starts late is better than a bot that dies mid-room.
            self.logger.error('Error in grace loop', exc_info=True)

    def _credit_early_start(self):
        """Earn the on-time minute for a race that started before its time.

        Judged by racetime's own start time: the loop only looks every 15
        seconds, so a race that began at 7:59:55 may be noticed after 8:00.
        """
        try:
            started_at = datetime.fromisoformat(
                (self.data.get('started_at') or '').replace('Z', '+00:00'))
        except ValueError:
            started_at = datetime.now(TIMEZONE)
        starters = entrants_from(
            self.data,
            monitors=self.data.get('monitors'),
            opened_by=self.data.get('opened_by'),
            statuses=STARTED,
        )
        decision = self.grace.started(started_at, starters)
        if decision.earn:
            self.grace_ledger.apply(decision, starters)
            self.logger.info('Grace: %s started early; %d racer(s) earn a minute',
                             self.data.get('name'), len(decision.earn))

    async def ex_grace(self, args, message):
        """`!grace` - your own balance, or a named racer's for anyone."""
        ledger = self.grace_ledger
        if ledger is None:
            return
        if args:
            wanted = ' '.join(args).lstrip('@').lower()
            for user_id, name in ledger.names.items():
                if name.lower() == wanted:
                    await self.send_message('{} has {} grace minute(s).'.format(
                        name, ledger.balance(user_id)))
                    return
            await self.send_message(
                'No grace record for {} yet - they start on {}.'.format(wanted, GRACE_START))
            return
        user = (message.get('user') or {})
        if not user.get('id'):
            return
        await self.send_message('{}, you have {} grace minute(s).'.format(
            user.get('name') or 'you', ledger.balance(str(user['id']))))

    def _log_chat(self, message):
        """Append a chat message to the per-race log file."""
        if not message:
            return
        try:
            os.makedirs(CHAT_LOG_DIR, exist_ok=True)
            race_name = self.data.get('name', 'unknown').replace('/', '_')
            log_path = os.path.join(CHAT_LOG_DIR, f'{race_name}.log')
            timestamp = message.get('posted_at', '')
            is_bot = message.get('is_bot', False)
            is_system = message.get('is_system', False)
            if is_bot:
                user = message.get('bot') or 'unknown-bot'
            else:
                user = (message.get('user') or {}).get('name', 'system')
            text = message.get('message_plain', message.get('message', ''))
            tag = ' [bot]' if is_bot else (' [system]' if is_system else '')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f'[{timestamp}] {user}{tag}: {text}\n')
        except Exception:
            self.logger.error('Error writing chat log', exc_info=True)

    async def chat_message(self, data):
        """Handle incoming chat messages: commands, hash detection, confirms."""
        message = data.get('message', {})

        # Log ALL messages (including bot/system) before processing
        self._log_chat(message)

        if message.get('is_bot'):
            self.logger.info(
                '[%s] Live bot message: bot=%r user=%r',
                self.data.get('name'),
                message.get('bot'),
                (message.get('user') or {}).get('name'),
            )
            if (self._is_sahasrahbot_msg(message) and not self.sahasrahbot_present
                    and not self.sahasrahbot_overridden):
                self.sahasrahbot_present = True
                self.logger.info(
                    '[%s] SahasrahBot detected live - seed commands deferred',
                    self.data.get('name'),
                )
            if self.ttp_tournament_room:
                # TTPBot's own words are applied when it says them, so only
                # the other bots' are read back here.
                if message.get('bot') != 'TTPBot':
                    await self._ttp_set_roll(picks.roll_state([message], start=self.roll_state))
                return
            # Whichever bot rolled, the room has its seed. Matters after an
            # !ttpbot, when SahasrahBot may yet wake up and roll one.
            text = message.get('message_plain') or message.get('message') or ''
            if 'Seed rolling complete.' in text:
                self.seed_rolled = True
            return

        if message.get('is_system'):
            sys_text = message.get('message_plain', '') or message.get('message', '')
            if 'personal best' in sys_text.lower():
                pb_match = re.match(r'(.+?)#\d+\s+', sys_text)
                if pb_match:
                    self.recap_data['pbs'].append(pb_match.group(1))
            return

        text = message.get('message', '').strip()
        user = message.get('user', {}).get('name', '')

        # Check for !commands first (via parent)
        words = text.lower().split()
        if words and words[0].startswith(self.command_prefix.lower()):
            method = 'ex_' + words[0][len(self.command_prefix):]
            args = text.split()[1:]  # preserve original case for flag strings
            command = words[0][len(self.command_prefix):]
            if self.ttp_tournament_room:
                try:
                    if await self._ttp_command(command, message):
                        return
                except Exception:
                    self.logger.error('Tournament pick command raised exception.', exc_info=True)
                    return
            if hasattr(self, method):
                self.logger.info('[%(race)s] Calling handler for %(word)s' % {
                    'race': self.data.get('name'),
                    'word': words[0],
                })
                try:
                    await getattr(self, method)(args, message)
                except Exception:
                    self.logger.error('Command raised exception.', exc_info=True)
            elif seasons.by_random_command(command):
                try:
                    await self._roll_season_random(seasons.by_random_command(command), message)
                except Exception:
                    self.logger.error('Command raised exception.', exc_info=True)
            elif registry.is_shortcut(command):
                # !consternation is how people ask for a preset. SahasrahBot has
                # no such command, so with it present this posts the flags.
                try:
                    await self._roll_unknown_to_sahasrahbot(command, message)
                except Exception:
                    self.logger.error('Command raised exception.', exc_info=True)
            return

        # Check for confirmation from a different user than who proposed the hash
        confirm_words = {
            'confirm', 'confirmed', 'y', 'yes', 'yep', 'yup', 'yeah',
            'affirmative', 'correct', 'good', 'matched', 'match', 'roger',
        }
        if text.lower() in confirm_words and self.pending_hash:
            if user != self.pending_hash_user:
                if self._is_active_participant(user):
                    await self._confirm_hash(confirmer=user, method='chat')
                else:
                    self.logger.info(
                        '[%s] Ignored hash confirm from %s (not active participant)',
                        self.data.get('name'), user,
                    )
            else:
                self.recap_data['self_confirm_attempts'].append(user)
            return

        # Check if the message is a 4-item ROM hash
        parsed, fuzzy_word, fuzzy_canonical = parse_hash(text)
        if parsed:
            if not self._is_active_participant(user):
                self.logger.info(
                    '[%s] Ignored hash proposal from %s (not active participant)',
                    self.data.get('name'), user,
                )
                return
            self.pending_hash = parsed
            self.pending_hash_user = user
            self.recap_data['hash_items'] = list(parsed)
            self.recap_data['hash_proposer'] = user
            self.recap_data['hash_confirmer'] = None
            self.recap_data['hash_confirm_method'] = None
            if fuzzy_word and fuzzy_canonical:
                _save_learned_alias(fuzzy_word, fuzzy_canonical)
                self.logger.info(
                    '[%s] Fuzzy-matched "%s" -> %s (learned)',
                    self.data.get('name'), fuzzy_word, fuzzy_canonical,
                )
            self.logger.info(
                '[%s] Hash proposed by %s: %s',
                self.data.get('name'), user, ' '.join(parsed),
            )

    def _is_active_participant(self, username):
        """Check if a user is an active race participant.

        Active means they are in the entrants list AND either:
        - have been joined for at least 30 seconds, OR
        - have a live stream
        """
        now = datetime.utcnow()
        for entrant in self.data.get('entrants', []):
            if entrant.get('user', {}).get('name', '') == username:
                # Check stream status
                if entrant.get('stream_live', False):
                    return True
                # Check join duration (at least 30 seconds)
                joined = entrant.get('joined')
                if joined:
                    if isinstance(joined, str):
                        try:
                            joined_dt = datetime.fromisoformat(
                                joined.replace('Z', '+00:00')
                            ).replace(tzinfo=None)
                        except (ValueError, TypeError):
                            return False
                    else:
                        joined_dt = joined
                    if (now - joined_dt).total_seconds() >= 30:
                        return True
                return False
        return False

    async def _confirm_hash(self, confirmer=None, method='chat'):
        """Append the confirmed hash to the race info."""
        hash_str = ' '.join(self.pending_hash)
        current_info = self.data.get('info_bot', '') or ''

        if '- Hash:' in current_info:
            # Replace existing hash
            idx = current_info.index('- Hash:')
            new_info = current_info[:idx] + f'- Hash: {hash_str}'
        elif current_info:
            new_info = f'{current_info} - Hash: {hash_str}'
        else:
            new_info = f'Hash: {hash_str}'

        await self.set_bot_raceinfo(new_info)
        self.recap_data['hash_confirmer'] = confirmer
        self.recap_data['hash_confirm_method'] = method
        self.logger.info(
            '[%s] Hash confirmed: %s',
            self.data.get('name'), hash_str,
        )
        self.pending_hash = None
        self.pending_hash_user = None

    async def race_data(self, data):
        await super().race_data(data)

        status = self.data.get('status', {}).get('value')
        if status == 'in_progress' and self.reminder_task:
            if not self.reminder_task.done():
                self.reminder_task.cancel()

        # Detect newly readied entrants for hash confirmation
        if self.pending_hash:
            current_ready = set()
            for entrant in self.data.get('entrants', []):
                entrant_status = entrant.get('status', {}).get('value', '')
                entrant_name = entrant.get('user', {}).get('name', '')
                if entrant_status == 'ready':
                    current_ready.add(entrant_name)

            newly_ready = current_ready - self.ready_entrants
            self.ready_entrants = current_ready

            # If anyone other than the hash poster just readied up, confirm
            confirming = newly_ready - {self.pending_hash_user}
            if confirming:
                confirmer_name = next(iter(confirming))
                self.logger.info(
                    '[%s] Hash confirmed by ready-up from: %s',
                    self.data.get('name'), ', '.join(confirming),
                )
                await self._confirm_hash(
                    confirmer=confirmer_name, method='ready-up',
                )

        if self.ttp_tournament_room:
            try:
                await self._ttp_race_data()
            except Exception:
                # The race goes on whatever the bracket says.
                self.logger.error('Tournament pick check raised exception.', exc_info=True)

    async def end(self):
        if self.state.get('_autumn_send_invites') == self._send_autumn_invites:
            self.state.pop('_autumn_send_invites', None)
        if self._ttp_task and not self._ttp_task.done():
            self._ttp_task.cancel()
        if self.reminder_task and not self.reminder_task.done():
            self.reminder_task.cancel()
        if self.grace_task and not self.grace_task.done():
            self.grace_task.cancel()

        # A finished League race records itself. end() also fires for a
        # cancelled room, which the recorder ignores: there is no result.
        # It waits for the room to settle before filing, so it runs on its own
        # rather than holding the room's end for two minutes.
        if self.league_room and self.results_recorder is not None:
            recorder, data, logger = self.results_recorder, dict(self.data), self.logger

            async def record():
                try:
                    await recorder.record(data)
                except Exception:
                    logger.exception('League result could not be recorded')
            task = asyncio.get_running_loop().create_task(record())
            _LEAGUE_RESULTS.add(task)
            task.add_done_callback(_LEAGUE_RESULTS.discard)

        event = self._tournament_event()
        collector = getattr(self, event + '_results', None) if event else None
        if collector is not None:
            try:
                await collector.record(self.data)
            except Exception:
                self.logger.exception('Tournament result suggestion pending recovery')

    async def ex_schedule(self, args, message):
        """!schedule - Show today's remaining race times."""
        now = datetime.now(TIMEZONE)
        upcoming = get_todays_remaining_races(now)

        if not upcoming:
            await self.send_message("No more TTP races scheduled for today.")
            return

        lines = ["Upcoming TTP races (Eastern):"]
        for race_time in upcoming:
            lines.append(f"  {race_time.strftime('%I:%M %p %Z')}")
        await self.send_message('\n'.join(lines))

    async def ex_info(self, args, message):
        """!info - Show the current TTP season's information."""
        await self.send_message(info_text(seasons.current(datetime.now(TIMEZONE))))

    async def ex_ttpflags(self, args, message):
        """!ttpflags - Show TTP flagset presets."""
        await self.send_message(
            flags_text(seasons.current(datetime.now(TIMEZONE))) +
            "In-season races have no required flagset -- flags are chosen by "
            "mutual agreement (majority vote if disagreement). The three "
            "official flagsets are encouraged but not required during the season."
        )

    async def _roll_seed_raceinfo(self, seed_str):
        """Update race info, preserving any existing scheduling prefix.

        Strip any previous seed segment before adding the new one.
        """
        current = self.data.get('info_bot', '') or ''
        # Strip any previously-written seed segment
        for marker in ('| Seed:', '| Flags:'):
            if marker in current:
                current = current[:current.index(marker)].rstrip()
        new_info = f'{current} | {seed_str}' if current else seed_str
        await self.set_bot_raceinfo(new_info)

    async def ex_flags(self, args, message):
        """!flags <flagstring> -- Roll a seed with a custom flag string.

        Z1R flagstrings are single tokens; only args[0] is used.
        """
        if self.sahasrahbot_present:
            return

        if self.seed_rolled:
            await self.send_message('A seed has already been rolled for this race.')
            return

        if not args:
            await self.send_message('You must specify a set of flags!')
            return

        # Brief wait: let SahasrahBot respond first if present but not yet detected
        await asyncio.sleep(2)
        if self.sahasrahbot_present or self.seed_rolled:
            return

        flags = args[0]
        registered = registry.is_registered(flags)
        # !flags is literal, like SahasrahBot's: it never rolls a shortcut for you.
        if not registered and registry.is_shortcut(flags):
            await self.send_message(f'"{flags}" is a shortcut, not a flag string. Use !{reduce_word(flags)} to roll it.')
            return
        try:
            decode_flags(flags)
        except FlagStringError:
            # "!flags doesn't work either" once rolled a seed for "doesn't".
            await self.send_message(f'"{flags}" is not a flag string. Usage: !flags <flagstring>')
            return
        if not registered and len(flags) < MIN_FLAG_STRING_LENGTH:
            # The decoder takes any letters, so a typo of a shortcut would roll a seed.
            await self.send_message(f'"{flags}" is too short to be a flag string. Usage: !flags <flagstring>')
            return
        seed = random.randint(0, 8999999999999999999)
        seed_str = f'Seed: {seed} - Flags: {flags}'

        self.seed_rolled = True  # Set before awaits to block concurrent invocations
        await self._roll_seed_raceinfo(seed_str)
        await self.send_message(seed_str)
        await self.send_message('Seed rolling complete.  See race info for details.')
        self.logger.info('[%s] Seed rolled via !flags: %s', self.data.get('name'), seed_str)
        if self.ttp_tournament_room:
            await self._ttp_set_roll(picks.ROLLED)

    @staticmethod
    def _is_ttpbot_command(message):
        text = (message.get('message') or message.get('message_plain') or '').strip()
        words = text.lower().split()
        return bool(words) and words[0] == '!ttpbot'

    async def ex_ttpbot(self, args, message):
        """!ttpbot -- Roll seeds here because SahasrahBot is not answering.

        SahasrahBot can greet a room and then go silent, and TTPBot, having
        seen the greeting, stays out of its way. This tells it to step in.
        Not !override: SahasrahBot already answers that one (it waives the
        stream requirement), so both bots would act on it.
        """
        if self.sahasrahbot_overridden:
            await self.send_message('TTPBot is already rolling seeds in this room.')
            return
        if not self.sahasrahbot_present:
            await self.send_message(
                "SahasrahBot hasn't been seen here, so TTPBot is already rolling seeds. "
                'Use !flags <flagstring> or !race <preset>.'
            )
            return
        self.sahasrahbot_overridden = True
        self.sahasrahbot_present = False
        self.logger.info('[%s] SahasrahBot overridden by %s', self.data.get('name'),
                         (message.get('user') or {}).get('name'))
        await self.send_message(
            'TTPBot will roll seeds in this room instead of SahasrahBot. '
            'Use !flags <flagstring>, !race <preset>, or a preset command like !ttp5.'
        )

    async def ex_cancel(self, args, message):
        """!cancel -- Clear the rolled seed so a new one can be rolled.

        SahasrahBot answers this itself when it is the one rolling seeds.
        """
        if self.sahasrahbot_present:
            return
        if not self.seed_rolled:
            await self.send_message('No seed has been rolled yet.')
            return
        self.seed_rolled = False
        await self.send_message('Seed cleared. You may now roll a new one.')
        if self.ttp_tournament_room:
            # Race info still shows the old string; this is what says it is gone.
            await self._ttp_set_roll(picks.CANCELLED)

    async def ex_summary(self, args, message):
        """!summary [flagstring|preset] -- Describe a flag string in plain words.

        Informational, so it answers even with SahasrahBot present. With no
        flag string it reads the one this room's seed was rolled with.
        """
        # A shortcut, e.g. !summary ttp5uphill, stands for its latest string.
        flags = (registry.resolve(args[0]) or args[0]) if args else self._room_flag_string()
        if not flags:
            await self.send_message(
                'Usage: !summary <flagstring> (no seed has been rolled here yet)'
            )
            return
        try:
            replies = format_summary(flags)
        except FlagStringError:
            await self.send_message("Couldn't read that flag string.")
            return
        for reply in replies:
            await self.send_message(reply)

    def _room_flag_string(self):
        """The flag string in this room's race info, if a seed has been rolled."""
        for key in ('info_bot', 'info_user'):
            match = re.search(r'Flags: (\S+)', self.data.get(key, '') or '')
            if match:
                return match.group(1)
        return None

    async def ex_race(self, args, message):
        """!race <preset> -- Roll a seed by named preset."""
        if self.ttp_tournament_room:
            # Each game's flagset is picked; a preset would roll around the pick.
            await self.send_message(picks.PRESET_OFF)
            return
        if self.sahasrahbot_present:
            return

        if self.seed_rolled:
            await self.send_message('A seed has already been rolled for this race.')
            return

        if not args:
            await self.send_message(f'No preset specified. {PRESET_LIST}')
            return

        preset = PRESET_ALIASES.get(args[0].lower(), args[0].lower())
        if registry.resolve(args[0]) is None:
            await self.send_message(f'Unknown preset "{preset}". {PRESET_LIST}')
            return

        # Brief wait: let SahasrahBot respond first if present but not yet detected
        await asyncio.sleep(2)
        if self.sahasrahbot_present or self.seed_rolled:
            return

        flags = registry.resolve(args[0])
        if flags is None:  # Deleted during the wait.
            return
        seed = random.randint(0, 8999999999999999999)
        seed_str = f'Flags: {flags} Seed: {seed}'

        self.seed_rolled = True  # Set before awaits to block concurrent invocations
        await self._roll_seed_raceinfo(seed_str)
        await self.send_message(f'{preset} - {seed_str}')
        await self.send_message('Seed rolling complete.  See race info for details.')
        self.logger.info('[%s] Seed rolled via !race %s: %s', self.data.get('name'), preset, seed_str)

    async def ex_ttp2(self, args, message):
        """!ttp2 -- Roll a random TTP Season 2 preset."""
        if self.sahasrahbot_present:
            return
        preset = random.choice(TTP2_PRESETS)
        await self.ex_race([preset], message)

    async def ex_ttp3(self, args, message):
        """!ttp3 -- Roll a random TTP Season 3 preset."""
        if self.sahasrahbot_present:
            return
        preset = random.choice(TTP3_PRESETS)
        await self.ex_race([preset], message)

    async def ex_ttp4(self, args, message):
        """!ttp4 -- Roll a random TTP Season 4 preset."""
        if self.sahasrahbot_present:
            return
        preset = random.choice(TTP4_PRESETS)
        await self.ex_race([preset], message)

    async def ex_ttp4rp(self, args, message):
        """!ttp4rp -- Roll the TTP4 Random% Remastered preset."""
        if self.sahasrahbot_present:
            return
        await self.ex_race(['ttp4rp'], message)

    async def ex_ttp4hopla(self, args, message):
        """!ttp4hopla -- Roll the TTP4 Hopla Remastered preset."""
        if self.sahasrahbot_present:
            return
        await self.ex_race(['ttp4hopla'], message)

    async def ex_ttp4consternation(self, args, message):
        """!ttp4consternation -- Roll the TTP4 Consternation Remastered preset."""
        if self.sahasrahbot_present:
            return
        await self.ex_race(['ttp4consternation'], message)

    async def ex_ttp5(self, args, message):
        """!ttp5 -- Roll a random TTP Season 5 preset."""
        season = seasons.by_random_command('ttp5')
        if season:
            await self._roll_season_random(season, message)
        else:
            await self._roll_unknown_to_sahasrahbot(random.choice(TTP5_PRESETS), message)

    async def _roll_season_random(self, season, message):
        """A season's random command: one of its three flagsets."""
        await self._roll_unknown_to_sahasrahbot(random.choice([entry['command'] for entry in season['flagsets']]), message)

    def _season_help_lines(self):
        """The !help lines for each season on file, oldest first."""
        lines = []
        for season in seasons.oldest_first():
            tag = f'TTP{season["number"]}'
            lines.append(f'    !{season["randomCommand"]:<26} Random {season["goal"]} preset')
            lines.append(f'    {" / ".join("!" + entry["command"] for entry in season["flagsets"])}  {tag} presets directly')
        return lines

    async def ex_ttp5uphill(self, args, message):
        """!ttp5uphill -- Roll the TTP5 Uphill Battle preset."""
        await self._roll_unknown_to_sahasrahbot('ttp5uphill', message)

    async def ex_ttp5muffle(self, args, message):
        """!ttp5muffle -- Roll the TTP5 Muffle Rug preset."""
        await self._roll_unknown_to_sahasrahbot('ttp5muffle', message)

    async def ex_ttp5pick5(self, args, message):
        """!ttp5pick5 -- Roll the TTP5 Pick 5 preset."""
        await self._roll_unknown_to_sahasrahbot('ttp5pick5', message)

    async def ex_tc34(self, args, message):
        """!tc34 -- Roll the Torneo Corto #34 flagset."""
        await self._roll_unknown_to_sahasrahbot('tc34', message)

    async def ex_tc33(self, args, message):
        """!tc33 -- Roll the Torneo Corto #33 flagset."""
        await self._roll_unknown_to_sahasrahbot('tc33', message)

    async def ex_tc32(self, args, message):
        """!tc32 -- Roll the Torneo Corto #32 flagset."""
        await self._roll_unknown_to_sahasrahbot('tc32', message)

    async def ex_tc31(self, args, message):
        """!tc31 -- Roll the Torneo Corto #31 flagset."""
        await self._roll_unknown_to_sahasrahbot('tc31', message)

    # Spelled out, for anybody who would rather type the whole thing. Both
    # forms are also presets, so !race tc34 and !summary tc34 already work.
    async def ex_torneocorto34(self, args, message):
        """!torneocorto34 -- Same as !tc34."""
        await self.ex_tc34(args, message)

    async def ex_torneocorto33(self, args, message):
        """!torneocorto33 -- Same as !tc33."""
        await self.ex_tc33(args, message)

    async def ex_torneocorto32(self, args, message):
        """!torneocorto32 -- Same as !tc32."""
        await self.ex_tc32(args, message)

    async def ex_torneocorto31(self, args, message):
        """!torneocorto31 -- Same as !tc31."""
        await self.ex_tc31(args, message)

    async def _roll_unknown_to_sahasrahbot(self, preset, message):
        """Roll a preset SahasrahBot has no command for.

        With SahasrahBot present TTPBot never rolls, to avoid two seeds, but
        staying silent would leave the command unanswered.
        """
        if self.ttp_tournament_room:
            await self.send_message(picks.PRESET_OFF)
            return
        flags = registry.resolve(preset)
        if flags is None:
            await self.send_message(f'Unknown preset "{preset}". {PRESET_LIST}')
            return
        if self.sahasrahbot_present:
            await self._hand_off_flags(registry.name_of(flags) or PRESET_NAMES.get(PRESET_ALIASES.get(preset, preset), preset), flags)
            return
        await self.ex_race([preset], message)

    async def _hand_off_flags(self, name, flags):
        # Kept off the start of the line so no bot reads it as a command.
        await self.send_message(f'{name} flags: {flags} -- roll with !flags {flags}')

    async def _league_week(self, week, message):
        name, flags = LEAGUE_WEEKS[week]
        if self.ttp_tournament_room:
            await self.send_message(picks.PRESET_OFF)
            return
        if self.sahasrahbot_present:
            # SahasrahBot has no League presets.
            await self._hand_off_flags(f'League Week {week} ({name})', flags)
            return
        await self.ex_flags([flags], message)

    async def ex_leagueweek1(self, args, message):
        """!leagueweek1 -- Roll the Z1RR League Week 1 flagset."""
        await self._league_week(1, message)

    async def ex_leagueweek2(self, args, message):
        """!leagueweek2 -- Roll the Z1RR League Week 2 flagset."""
        await self._league_week(2, message)

    async def ex_leagueweek3(self, args, message):
        """!leagueweek3 -- Roll the Z1RR League Week 3 flagset."""
        await self._league_week(3, message)

    async def ex_leagueweek4(self, args, message):
        """!leagueweek4 -- Roll the Z1RR League Week 4 flagset."""
        await self._league_week(4, message)

    async def ex_leagueweek5(self, args, message):
        """!leagueweek5 -- Roll the Z1RR League Week 5 flagset."""
        await self._league_week(5, message)

    async def ex_leagueweek6(self, args, message):
        """!leagueweek6 -- Roll the Z1RR League Week 6 flagset."""
        await self._league_week(6, message)

    async def ex_leagueweek7(self, args, message):
        """!leagueweek7 -- Roll the Z1RR League Week 7 flagset."""
        await self._league_week(7, message)

    async def ex_matchup(self, args, message):
        """!matchup <racer1> <racer2> -- Two racers' record against each other.

        Informational, so it answers even with SahasrahBot present.
        """
        if len(args) != 2:
            await self.send_message('Usage: !matchup <racer1> <racer2>')
            return
        await self.send_message(await matchup_reply(args[0], args[1]))

    async def ex_z1rr(self, args, message):
        """!z1rr -- Show the Z1RR Discord invite."""
        await self.send_message(f'Join the Z1RR Discord! {Z1RR_DISCORD_URL}')

    async def ex_optout(self, args, message):
        """Sit out this TC bracket, while still racing the qualifiers."""
        await self._tc_preference(args, message, True)

    async def ex_optin(self, args, message):
        """Undo a TC opt-out before invitations open."""
        await self._tc_preference(args, message, False)

    async def _tc_preference(self, args, message, declined):
        if args:
            await self.send_message('Use !optout or !optin with no name. These commands change only your own TC choice.')
            return
        if self.tc_preferences is None:
            await self.send_message('TC chat choices are unavailable. Please tell Council your choice.')
            return
        try:
            result = await self.tc_preferences.record(self.data['name'], message, declined)
        except Exception:
            self.logger.warning('TC chat choice could not be confirmed', exc_info=True)
            await self.send_message('I could not confirm your TC choice was saved. Try again, or tell Council.')
            return
        error = result.get('error')
        if error:
            text = {
                'outside-qualifiers':'!optout and !optin work only in current TC qualifier rooms tagged by Council.',
                'offers-started':'TC offers have started. Answer at https://my.z1rracing.com or ask Council to change your choice.',
                'no-edition':'Council needs to set the TC edition before I can save your choice. Please tell Council.',
                'wrong-night':'Council has a different qualifier night set up. Please tell Council your choice.',
            }.get(error, 'I could not confirm your TC choice was saved. Please tell Council.')
        elif isinstance(result.get('edition'), int) and isinstance(result.get('declined'), bool):
            if result['declined']:
                text = "Recorded: you're sitting out TC{}. You can still race the qualifiers. Use !optin before offers start to undo this.".format(result['edition'])
            else:
                text = "Recorded: you're eligible for TC{} again. This does not claim a seat; answer your invite if you qualify.".format(result['edition'])
        else:
            text = 'I could not confirm your TC choice was saved. Try again, or tell Council.'
        await self.send_message(text)

    async def ex_help(self, args, message):
        """!help -- List available TTPBot commands."""
        lines = [
            'TTPBot commands:',
            '  TC qualifiers: !optout sits out this TC bracket; !optin undoes it before offers start.',
            '  Seed rolling (when SahasrahBot is offline):',
            '    !ttpbot                     Roll seeds here when SahasrahBot is not answering',
            '    !cancel                     Clear the rolled seed to roll again',
            '    !race <preset>              Roll a seed by preset name',
            '    !flags <flagstring>         Roll a seed with a custom flag string',
            '    !ttp2                       Random TTP Season 2 preset',
            '    !ttp3                       Random TTP Season 3 preset',
            '    !ttp4                       Random TTP Season 4 preset',
            '    !ttp4rp / !ttp4hopla / !ttp4consternation  TTP4 presets directly',
            *self._season_help_lines(),
            '    !leagueweek1 ... !leagueweek7  Z1RR League weekly flagsets',
            '  Flags:',
            "    !summary [flagstring]       Summarize a flag string or preset (default: this room's seed)",
            '  Season info:',
            '    !schedule                   Today\'s remaining race times',
            f'    !info                       {seasons.current(datetime.now(TIMEZONE))["goal"]} details',
            '    !ttpflags                   TTP flagset details',
            '    !matchup <racer1> <racer2>  Head-to-head record from the Z1RR stats',
            '    !z1rr                       Z1RR Discord invite',
            '    !grace [name]               Grace minutes left before a forced start',
        ]
        await self.send_message('\n'.join(lines))


def _alias_command(preset):
    async def command(self, args, message):
        await self._roll_unknown_to_sahasrahbot(preset, message)
    command.__doc__ = f'Roll the {preset} preset.'
    return command


# Each alias is a command of its own, e.g. !ttp4rr rolls ttp4rp.
for _alias in PRESET_ALIASES:
    setattr(TTPRaceHandler, f'ex_{_alias}', _alias_command(_alias))
