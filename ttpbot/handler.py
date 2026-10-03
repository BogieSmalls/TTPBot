import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

import aiohttp
from racetime_bot import RaceHandler

from .flag_summary import FlagStringError, decode as decode_flags, format_summary
from .grace import GRACE_START, STARTED, GraceRace, entrants_from
from .matchup import matchup_reply

from .config import (
    HASH_ALIASES,
    HASH_ALIASES_MULTI,
    LEAGUE_ROOM_INFO_PREFIX,
    LEAGUE_WEEKS,
    PRESET_ALIASES,
    PRESET_NAMES,
    RACE_NUMBER_MAP,
    REMINDER_SCHEDULE,
    SEED_PRESETS,
    TTP2_PRESETS,
    TTP3_PRESETS,
    TTP4_PRESETS,
    TTP5_PRESETS,
    TIMEZONE,
    Z1RR_DISCORD_URL,
)
from .paths import ensure_parent_dir, runtime_path
from .room_policy import is_corto_room, is_autumn_room, is_league_room, is_ttp_scheduled_room
from .schedule import find_nearest_scheduled_race, get_todays_remaining_races

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
    grace_enforced = False

    stop_at = ['cancelled', 'finished']

    #: Set by TTPBot at start-up, as the grace ledger is. None when League
    #: result recording is switched off.
    results_recorder = None
    autumn_results = None
    corto_results = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reminders_sent = set()
        self.scheduled_time = None
        self.bot_created = False
        self.ttp_scheduled_room = False
        self.league_room = False
        self.autumn_room = False
        self.corto_room = False
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
            elif self.autumn_room or self.corto_room:
                # Shared state is reseeded after reconnects and process restarts.
                self.state['_autumn_send_invites'] = self._send_autumn_invites
                await self._send_autumn_invites()

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
        pairing = title[len(LEAGUE_ROOM_INFO_PREFIX):]
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

    async def _send_autumn_invites(self):
        """Invite an Autumn room's two racers, exactly once."""
        await self._send_invites(
            'Torneo Corto' if self.corto_room else 'Autumn', 'autumn_invited', self._autumn_invite_ids())

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

        # Also detect if a seed was already rolled (avoids double-roll on restart).
        # SahasrahBot's rolls count too, or a !ttpbot could roll a second
        # seed. A third bot sending "Seed rolling complete." would lock rolling.
        for msg in messages:
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
            'Welcome to TTP Season 5!' in text
            or 'Welcome to TTP Season 4!' in text
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
                await self.send_message(
                    "Welcome to TTP Season 5! I'll help out with hash "
                    "confirmation and other bot duties. "
                    "Type !schedule for today's race times, !info for TTP details, "
                    "or !ttpflags for flagset details."
                )
            else:
                await self.send_message(GENERIC_WELCOME_MESSAGE)
            self.state['welcomed'] = True

        await self._handle_recent_history_commands(messages)

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
            if hasattr(self, method):
                self.logger.info('[%(race)s] Calling handler for %(word)s' % {
                    'race': self.data.get('name'),
                    'word': words[0],
                })
                try:
                    await getattr(self, method)(args, message)
                except Exception:
                    self.logger.error('Command raised exception.', exc_info=True)
            elif command in SEED_PRESETS:
                # !consternation is how people ask for a preset; it means !race.
                try:
                    await self.ex_race([command], message)
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

    async def end(self):
        if self.state.get('_autumn_send_invites') == self._send_autumn_invites:
            self.state.pop('_autumn_send_invites', None)
        if self.reminder_task and not self.reminder_task.done():
            self.reminder_task.cancel()
        if self.grace_task and not self.grace_task.done():
            self.grace_task.cancel()

        # A finished League race records itself. end() also fires for a
        # cancelled room, which the recorder ignores: there is no result.
        if self.league_room and self.results_recorder is not None:
            try:
                await self.results_recorder.record(self.data)
            except Exception:
                self.logger.exception('League result could not be recorded')

        collector = self.corto_results if getattr(self, 'corto_room', False) else self.autumn_results if getattr(self, 'autumn_room', False) else None
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
        """!info - Show TTP Season 5 information."""
        await self.send_message(
            "TTP Season 5 regular season runs Aug 31 - Dec 19, 2026. "
            "Rooms use the TTP Season 5 goal during this window. "
            "Races: Mon-Sat at 8 PM, 10 PM, 12 AM ET, plus 6 PM on Saturday "
            "(the 12 AM race closes out the previous evening). "
            "No Sunday evening races."
        )

    async def ex_ttpflags(self, args, message):
        """!ttpflags - Show TTP flagset presets."""
        await self.send_message(
            "TTP5 flagset presets:\n"
            "  !ttp5 -- Random pick from the three TTP5 flagsets\n"
            "  !ttp5uphill -- Uphill Battle\n"
            "  !ttp5muffle -- Muffle Rug\n"
            "  !ttp5pick5 -- Pick 5\n"
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
        try:
            decode_flags(flags)
        except FlagStringError:
            # "!flags doesn't work either" once rolled a seed for "doesn't".
            await self.send_message(f'"{flags}" is not a flag string. Usage: !flags <flagstring>')
            return
        seed = random.randint(0, 8999999999999999999)
        seed_str = f'Seed: {seed} - Flags: {flags}'

        self.seed_rolled = True  # Set before awaits to block concurrent invocations
        await self._roll_seed_raceinfo(seed_str)
        await self.send_message(seed_str)
        await self.send_message('Seed rolling complete.  See race info for details.')
        self.logger.info('[%s] Seed rolled via !flags: %s', self.data.get('name'), seed_str)

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

    async def ex_summary(self, args, message):
        """!summary [flagstring|preset] -- Describe a flag string in plain words.

        Informational, so it answers even with SahasrahBot present. With no
        flag string it reads the one this room's seed was rolled with.
        """
        flags = args[0] if args else self._room_flag_string()
        if flags:
            # A preset name, e.g. !summary ttp5uphill, stands for its flags.
            preset = PRESET_ALIASES.get(flags.lower(), flags.lower())
            flags = SEED_PRESETS.get(preset, flags)
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
        if self.sahasrahbot_present:
            return

        if self.seed_rolled:
            await self.send_message('A seed has already been rolled for this race.')
            return

        if not args:
            presets = ', '.join(sorted(SEED_PRESETS.keys()))
            await self.send_message(f'No preset specified. Available presets: {presets}')
            return

        preset = PRESET_ALIASES.get(args[0].lower(), args[0].lower())
        if preset not in SEED_PRESETS:
            presets = ', '.join(sorted(SEED_PRESETS.keys()))
            await self.send_message(
                f'Unknown preset "{preset}". Available presets: {presets}'
            )
            return

        # Brief wait: let SahasrahBot respond first if present but not yet detected
        await asyncio.sleep(2)
        if self.sahasrahbot_present or self.seed_rolled:
            return

        flags = SEED_PRESETS[preset]
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
        await self._roll_unknown_to_sahasrahbot(random.choice(TTP5_PRESETS), message)

    async def ex_ttp5uphill(self, args, message):
        """!ttp5uphill -- Roll the TTP5 Uphill Battle preset."""
        await self._roll_unknown_to_sahasrahbot('ttp5uphill', message)

    async def ex_ttp5muffle(self, args, message):
        """!ttp5muffle -- Roll the TTP5 Muffle Rug preset."""
        await self._roll_unknown_to_sahasrahbot('ttp5muffle', message)

    async def ex_ttp5pick5(self, args, message):
        """!ttp5pick5 -- Roll the TTP5 Pick 5 preset."""
        await self._roll_unknown_to_sahasrahbot('ttp5pick5', message)

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
    # forms are also presets, so !race tc33 and !summary tc33 already work.
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
        if self.sahasrahbot_present:
            await self._hand_off_flags(PRESET_NAMES.get(preset, preset), SEED_PRESETS[preset])
            return
        await self.ex_race([preset], message)

    async def _hand_off_flags(self, name, flags):
        # Kept off the start of the line so no bot reads it as a command.
        await self.send_message(f'{name} flags: {flags} -- roll with !flags {flags}')

    async def _league_week(self, week, message):
        name, flags = LEAGUE_WEEKS[week]
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

    async def ex_help(self, args, message):
        """!help -- List available TTPBot commands."""
        lines = [
            'TTPBot commands:',
            '  Seed rolling (when SahasrahBot is offline):',
            '    !ttpbot                     Roll seeds here when SahasrahBot is not answering',
            '    !cancel                     Clear the rolled seed to roll again',
            '    !race <preset>              Roll a seed by preset name',
            '    !flags <flagstring>         Roll a seed with a custom flag string',
            '    !ttp2                       Random TTP Season 2 preset',
            '    !ttp3                       Random TTP Season 3 preset',
            '    !ttp4                       Random TTP Season 4 preset',
            '    !ttp4rp / !ttp4hopla / !ttp4consternation  TTP4 presets directly',
            '    !ttp5                       Random TTP Season 5 preset',
            '    !ttp5uphill / !ttp5muffle / !ttp5pick5  TTP5 presets directly',
            '    !leagueweek1 ... !leagueweek7  Z1RR League weekly flagsets',
            '  Flags:',
            "    !summary [flagstring]       Summarize a flag string or preset (default: this room's seed)",
            '  Season info:',
            '    !schedule                   Today\'s remaining race times',
            '    !info                       TTP Season 5 details',
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
for _alias, _preset in PRESET_ALIASES.items():
    setattr(TTPRaceHandler, f'ex_{_alias}', _alias_command(_preset))
