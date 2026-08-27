import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

import aiohttp
from racetime_bot import RaceHandler

from .config import (
    HASH_ALIASES,
    HASH_ALIASES_MULTI,
    RACE_NUMBER_MAP,
    REMINDER_SCHEDULE,
    SEED_PRESETS,
    TTP2_PRESETS,
    TTP3_PRESETS,
    TTP4_PRESETS,
    TIMEZONE,
    Z1RR_DISCORD_URL,
)
from .paths import ensure_parent_dir, runtime_path
from .room_policy import is_ttp_scheduled_room
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

    stop_at = ['cancelled', 'finished']

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reminders_sent = set()
        self.scheduled_time = None
        self.bot_created = False
        self.ttp_scheduled_room = False
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
        self.seed_rolled = False
        self.history_command_cutoff_utc = None

    async def begin(self):
        self.ttp_scheduled_room = is_ttp_scheduled_room(self.data)
        self.history_command_cutoff_utc = self._recent_room_history_cutoff()

        if self.ttp_scheduled_room:
            self._determine_scheduled_time()

            # Detect if this room was created by the bot (has "Scheduled:" in info)
            info_bot = self.data.get('info_bot', '') or ''
            self.bot_created = 'Scheduled:' in info_bot

            if self.scheduled_time:
                now = datetime.now(TIMEZONE)
                minutes_until = (self.scheduled_time - now).total_seconds() / 60

                if minutes_until >= -1:
                    # Race time is upcoming or just arrived - send reminders.
                    # Pre-mark reminders whose window is well past (>2 min ago)
                    # so a service restart doesn't dump all reminders at once.
                    for minutes_before, _ in REMINDER_SCHEDULE:
                        if minutes_until < minutes_before - 2:
                            self.reminders_sent.add(minutes_before)

                    self.reminder_task = asyncio.ensure_future(self._reminder_loop())
                # If past the start time: skip reminders but still welcome.
        else:
            self.scheduled_time = None
            self.bot_created = False

        # Request chat history to detect prior seed rolls and, for TTP rooms,
        # avoid duplicate welcomes/reminders.
        await self.ws.send(json.dumps({'action': 'gethistory'}))

    async def chat_history(self, data):
        """Check chat history for existing bot messages to avoid duplicates."""
        messages = data.get('messages', [])

        # Also detect if a seed was already rolled (avoids double-roll on restart).
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

        if already_welcomed:
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
            if hasattr(self, method):
                self.logger.info('[%(race)s] Calling handler for %(word)s' % {
                    'race': self.data.get('name'),
                    'word': words[0],
                })
                try:
                    await getattr(self, method)(args, message)
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
        if self.reminder_task and not self.reminder_task.done():
            self.reminder_task.cancel()

        pass

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
            "Rooms use the TTP: Season 5 goal during this window. "
            "Races: Mon-Fri at 8 PM, 10 PM, 12 AM ET | "
            "Sat at 12 PM, 3 PM, 6 PM ET (plus 12 AM from Friday). "
            "No races on Sunday."
        )

    async def ex_ttpflags(self, args, message):
        """!ttpflags - Show TTP flagset presets."""
        await self.send_message(
            "TTP flagset presets:\n"
            "  !ttp4 -- Random pick from the three TTP4 flagsets\n"
            "  !ttp4rp -- Random% Remastered\n"
            "  !ttp4hopla -- Hopla Remastered\n"
            "  !ttp4consternation -- Consternation Remastered\n"
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
        if self.seed_rolled:
            await self.send_message('A seed has already been rolled for this race.')
            return

        if not args:
            await self.send_message('You must specify a set of flags!')
            return

        if self.seed_rolled:
            return

        flags = args[0]
        seed = random.randint(0, 8999999999999999999)
        seed_str = f'Seed: {seed} - Flags: {flags}'

        self.seed_rolled = True  # Set before awaits to block concurrent invocations
        await self._roll_seed_raceinfo(seed_str)
        await self.send_message(seed_str)
        await self.send_message('Seed rolling complete.  See race info for details.')
        self.logger.info('[%s] Seed rolled via !flags: %s', self.data.get('name'), seed_str)

    async def ex_race(self, args, message):
        """!race <preset> -- Roll a seed by named preset."""
        if self.seed_rolled:
            await self.send_message('A seed has already been rolled for this race.')
            return

        if not args:
            presets = ', '.join(sorted(SEED_PRESETS.keys()))
            await self.send_message(f'No preset specified. Available presets: {presets}')
            return

        preset = args[0].lower()
        if preset not in SEED_PRESETS:
            presets = ', '.join(sorted(SEED_PRESETS.keys()))
            await self.send_message(
                f'Unknown preset "{preset}". Available presets: {presets}'
            )
            return

        if self.seed_rolled:
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
        preset = random.choice(TTP2_PRESETS)
        await self.ex_race([preset], message)

    async def ex_ttp3(self, args, message):
        """!ttp3 -- Roll a random TTP Season 3 preset."""
        preset = random.choice(TTP3_PRESETS)
        await self.ex_race([preset], message)

    async def ex_ttp4(self, args, message):
        """!ttp4 -- Roll a random TTP Season 4 preset."""
        preset = random.choice(TTP4_PRESETS)
        await self.ex_race([preset], message)

    async def ex_ttp4rp(self, args, message):
        """!ttp4rp -- Roll the TTP4 Random% Remastered preset."""
        await self.ex_race(['ttp4rp'], message)

    async def ex_ttp4hopla(self, args, message):
        """!ttp4hopla -- Roll the TTP4 Hopla Remastered preset."""
        await self.ex_race(['ttp4hopla'], message)

    async def ex_ttp4consternation(self, args, message):
        """!ttp4consternation -- Roll the TTP4 Consternation Remastered preset."""
        await self.ex_race(['ttp4consternation'], message)

    async def ex_z1rr(self, args, message):
        """!z1rr -- Show the Z1RR Discord invite."""
        await self.send_message(f'Join the Z1RR Discord! {Z1RR_DISCORD_URL}')

    async def ex_help(self, args, message):
        """!help -- List available TTPBot commands."""
        lines = [
            'TTPBot commands:',
            '  Seed rolling:',
            '    !race <preset>              Roll a seed by preset name',
            '    !flags <flagstring>         Roll a seed with a custom flag string',
            '    !ttp2                       Random TTP Season 2 preset',
            '    !ttp3                       Random TTP Season 3 preset',
            '    !ttp4                       Random TTP Season 4 preset',
            '    !ttp4rp / !ttp4hopla / !ttp4consternation  TTP4 presets directly',
            '  Season info:',
            '    !schedule                   Today\'s remaining race times',
            '    !info                       TTP Season 5 details',
            '    !ttpflags                   TTP flagset details',
            '    !z1rr                       Z1RR Discord invite',
        ]
        await self.send_message('\n'.join(lines))
