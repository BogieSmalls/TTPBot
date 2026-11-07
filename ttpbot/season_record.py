"""The TTP season record: a season's dates, goal and copy, without a deploy.

Council edits it, the site render publishes https://z1rracing.com/ttp-seasons.json,
and this polls that file, the way flagset_registry does. A file is checked whole; a
bad one changes nothing, and the last good copy stays, here and on disk. Season 5's
built-in copy applies only until a first load.

Imports only config, so schedule and room_policy can use it with no import cycle.
validate_snapshot is the same contract as Z1RR.Data's and the site's; the shared
proof is tests/fixtures/ttp-seasons-contract.json.
"""
import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import aiohttp

from .config import GOAL_NAME, REGULAR_SEASON_ROOM_INFO_PREFIX, SEASON_END, SEASON_START

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 300
FETCH_TIMEOUT_SECONDS = 10
#: A race before 6 AM ET belongs to the night before, as every reader has it.
NIGHT_ROLLOVER_HOUR = 6

TEXT_MAX = 200
FIELD_MIN = 8
FIELD_MAX = 128
MAX_SAFE_INTEGER = 2 ** 53 - 1
DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
INSTANT = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?Z')
CLOCK = re.compile(r'([01][0-9]|2[0-3]):[0-5][0-9]')
SLUG = re.compile(r'[a-z0-9_]{1,60}')
SNOWFLAKE = re.compile(r'[0-9]{5,25}')

#: Season 5 as the rules doc has it, for every use until the first file arrives.
BUILT_IN = {
    'number': 5,
    'theme': "Five Hearts at Betty's",
    'rulesUrl': 'https://docs.google.com/document/d/1JVLZdgtF_edcJMB0vrI5NKUlj84Yq0uJ7q1Yqn5V3A4/edit?tab=t.0',
    'goal': GOAL_NAME,
    'roomPrefix': REGULAR_SEASON_ROOM_INFO_PREFIX,
    'boardLabel': 'TTP S5',
    'discordChannelId': '1494388330022830251',
    'discordChannelName': 'ttp5',
    'adminTeam': [],
    'schedule': {
        'firstNight': SEASON_START.isoformat(),
        'lastNight': SEASON_END.isoformat(),
        'weeks': 16,
        'slots': [
            {'name': 'TTP0', 'at': '18:00', 'days': [6]},
            {'name': 'TTP1', 'at': '20:00', 'days': [1, 2, 3, 4, 5, 6]},
            {'name': 'TTP2', 'at': '22:00', 'days': [1, 2, 3, 4, 5, 6]},
            {'name': 'TTP3', 'at': '00:00', 'days': [1, 2, 3, 4, 5, 6]},
        ],
        'afterLastRace': 'stop',
    },
    'flagsets': [
        {'name': 'TTP5 Uphill Battle', 'command': 'ttp5uphill'},
        {'name': 'TTP5 Muffle Rug', 'command': 'ttp5muffle'},
        {'name': 'TTP5 Pick 5', 'command': 'ttp5pick5'},
    ],
    'randomCommand': 'ttp5',
    'qualifying': {'races': 16, 'fieldLimit': None, 'tiebreakers': ['head-to-head wins', 'average finish time']},
    'tournament': {
        'answersDueAt': '2026-12-29T05:00:00.000Z',
        'reminderAt': '2026-12-26T05:00:00.000Z',
        'goal': 'Beat the game',
        'roomLabel': 'TTP Season 5 Tournament',
        'challongeSlug': 'z1rrttp5',
        'predictionsCloseAt': '2027-01-04T05:00:00.000Z',
        'round1Date': '2027-01-04',
        'roundDays': 7,
    },
    'handChecklist': {'rulesDoc': True, 'goalExists': True, 'discordChannels': False, 'predictionsOpened': False},
}


def _reduce(text):
    return re.sub(r'[^a-z0-9]', '', str(text if text is not None else '').lower())


def _is_int(value, low=None):
    return (isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_SAFE_INTEGER
            and (low is None or value >= low))


def _is_text(value):
    return isinstance(value, str) and value.strip() != '' and len(value) <= TEXT_MAX


def _as_date(value):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_instant(value):
    match = INSTANT.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return False
    try:
        datetime(*(int(part) for part in match.groups()[:6]))
    except ValueError:
        return False
    return True


def _js_weekday(day):
    """Sunday is 0 in the record, as in every other reader."""
    return (day.weekday() + 1) % 7


def _schedule_problems(schedule, say):
    first, last = _as_date(schedule.get('firstNight')), _as_date(schedule.get('lastNight'))
    if first is None:
        say('firstNight must be a date')
    elif _js_weekday(first) != 1:
        say(f'first night {schedule["firstNight"]} is not a Monday')
    if last is None:
        say('lastNight must be a date')
    elif _js_weekday(last) != 6:
        say(f'last night {schedule["lastNight"]} is not a Saturday')
    weeks = schedule.get('weeks')
    if not _is_int(weeks, 1):
        say('weeks must be a whole number')
    elif first and last and _js_weekday(first) == 1 and _js_weekday(last) == 6:
        made = ((last - first).days + 2) / 7
        if made != weeks:
            say(f'weeks says {weeks}, but the dates make {made:g} weeks')
    slots = schedule.get('slots')
    if not isinstance(slots, list) or not slots:
        say('at least one slot required')
    else:
        for slot in slots:
            days = slot.get('days') if isinstance(slot, dict) else None
            good = (isinstance(slot, dict) and _is_text(slot.get('name'))
                    and isinstance(slot.get('at'), str) and CLOCK.fullmatch(slot['at'])
                    and isinstance(days, list) and days
                    and all(_is_int(day) and 0 <= day <= 6 for day in days)
                    and len(set(days)) == len(days))
            if not good:
                say(f'invalid slot {json.dumps(slot)}')
    if schedule.get('afterLastRace') not in ('stop', 'continue'):
        say('afterLastRace must be stop or continue')


def _flagset_problems(flagsets, say):
    if not isinstance(flagsets, list) or len(flagsets) != 3:
        say('exactly three flagsets required')
        return
    if any(not isinstance(entry, dict) or not _is_text(entry.get('name')) or not _reduce(entry.get('name'))
           or not _is_text(entry.get('command')) or not _reduce(entry.get('command')) for entry in flagsets):
        say('each of the three flagsets needs a name and a command')
        return
    for key in ('name', 'command'):
        if len({_reduce(entry[key]) for entry in flagsets}) != 3:
            say(f'the three flagsets need distinct {key}s')


def _season_problems(season):
    if not isinstance(season, dict):
        return ['a season must be an object']
    label = f'season {season["number"]}' if _is_int(season.get('number')) else 'a season'
    problems = []

    def say(text):
        problems.append(f'{label}: {text}')

    if not _is_int(season.get('number'), 1):
        say('invalid number')
    for key in ('theme', 'goal', 'roomPrefix', 'boardLabel', 'discordChannelName', 'randomCommand'):
        if not _is_text(season.get(key)):
            say(f'invalid {key}')
    rules = season.get('rulesUrl')
    if rules is not None and not (isinstance(rules, str) and rules.lower().startswith('https://') and len(rules) > 8):
        say('rulesUrl must be an https link or empty')
    channel = season.get('discordChannelId')
    if not isinstance(channel, str) or not SNOWFLAKE.fullmatch(channel):
        say('invalid discordChannelId')
    admins = season.get('adminTeam')
    if not isinstance(admins, list) or any(not _is_text(name) for name in admins):
        say('invalid adminTeam')
    if not isinstance(season.get('schedule'), dict):
        say('schedule required')
    else:
        _schedule_problems(season['schedule'], say)
    _flagset_problems(season.get('flagsets'), say)
    qualifying = season.get('qualifying')
    if not isinstance(qualifying, dict):
        say('qualifying required')
    else:
        if not _is_int(qualifying.get('races'), 1):
            say('invalid qualifying races')
        limit = qualifying.get('fieldLimit')
        if limit is not None and not (_is_int(limit) and FIELD_MIN <= limit <= FIELD_MAX):
            say(f'fieldLimit must be {FIELD_MIN} to {FIELD_MAX} or empty')
        rules_list = qualifying.get('tiebreakers')
        if not isinstance(rules_list, list) or any(not _is_text(rule) for rule in rules_list):
            say('invalid tiebreakers')
    tournament = season.get('tournament')
    if not isinstance(tournament, dict):
        say('tournament required')
    else:
        for key in ('answersDueAt', 'reminderAt', 'predictionsCloseAt'):
            if not _is_instant(tournament.get(key)):
                say(f'invalid {key}')
        for key in ('goal', 'roomLabel'):
            if not _is_text(tournament.get(key)):
                say(f'invalid {key}')
        slug = tournament.get('challongeSlug')
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            say('invalid challongeSlug')
        if _as_date(tournament.get('round1Date')) is None:
            say('invalid round1Date')
        if not _is_int(tournament.get('roundDays'), 1):
            say('invalid roundDays')
    checklist = season.get('handChecklist')
    if not isinstance(checklist, dict) or any(not isinstance(done, bool) for done in checklist.values()):
        say('invalid handChecklist')
    return problems


def validate_snapshot(snapshot):
    """Problems with a whole snapshot; empty when valid. Same contract as every reader."""
    if not isinstance(snapshot, dict):
        return ['snapshot must be an object']
    problems = []
    if not _is_int(snapshot.get('schemaVersion')) or snapshot.get('schemaVersion') != 1:
        problems.append('unsupported schema')
    if not _is_int(snapshot.get('revision'), 1):
        problems.append('invalid revision')
    listed = snapshot.get('seasons')
    if not isinstance(listed, list) or not listed:
        return problems + ['at least one season required']
    seen = set()
    for season in listed:
        problems.extend(_season_problems(season))
        number = season.get('number') if isinstance(season, dict) else None
        if isinstance(number, int) and number in seen:
            problems.append(f'season {number} twice')
        if isinstance(number, int):
            seen.add(number)
    return problems


def first_night(season):
    return date.fromisoformat(season['schedule']['firstNight'])


def last_night(season):
    return date.fromisoformat(season['schedule']['lastNight'])


def slots_on_night(season, night):
    """A season's slots on a night, whichever date they land on."""
    return [slot for slot in season['schedule']['slots'] if _js_weekday(night) in slot['days']]


def _clock(at):
    """'20:00' as people say it: '8 PM'. Minutes only when there are any."""
    hour, minute = (int(part) for part in at.split(':'))
    shown = f'{hour % 12 or 12}{f":{minute:02d}" if minute else ""}'
    return f'{shown} {"AM" if hour < 12 else "PM"}'


def _short_date(day):
    return f'{day.strftime("%b")} {day.day}'


DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
DAY_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


def _day_range(days):
    """Monday first, as the week reads here: 'Mon-Sat', or 'Mon, Wed'."""
    ordered = sorted(days, key=lambda day: (day + 6) % 7)
    if len(ordered) == 1:
        return DAY_LONG[ordered[0]]
    positions = [(day + 6) % 7 for day in ordered]
    if positions == list(range(positions[0], positions[0] + len(positions))):
        return f'{DAY_SHORT[ordered[0]]}-{DAY_SHORT[ordered[-1]]}'
    return ', '.join(DAY_SHORT[day] for day in ordered)


def _races_sentence(season):
    """'Mon-Sat at 8 PM, 10 PM, 12 AM ET, plus 6 PM on Saturday (the 12 AM race closes out the previous evening).'"""
    slots = season['schedule']['slots']
    widest = max(len(slot['days']) for slot in slots)
    common = next(sorted(slot['days']) for slot in slots if len(slot['days']) == widest)
    main = [slot for slot in slots if sorted(slot['days']) == common]
    others = [slot for slot in slots if sorted(slot['days']) != common]
    sentence = f'{_day_range(common)} at {", ".join(_clock(slot["at"]) for slot in main)} ET'
    for slot in others:
        sentence += f', plus {_clock(slot["at"])} on {_day_range(slot["days"])}'
    late = [slot for slot in slots if int(slot['at'].split(':')[0]) < NIGHT_ROLLOVER_HOUR]
    if late:
        names = ' and '.join(_clock(slot['at']) for slot in late)
        sentence += f' (the {names} race closes out the previous evening)'
    return sentence + '.'


def welcome_text(season):
    return (f"Welcome to {season['goal']}! I'll help out with hash "
            "confirmation and other bot duties. "
            "Type !schedule for today's race times, !info for TTP details, "
            "or !ttpflags for flagset details.")


def info_text(season):
    first, last = first_night(season), last_night(season)
    goal = season['goal']
    text = (f'{goal} regular season runs {_short_date(first)} - {_short_date(last)}, {last.year}. '
            f'Rooms use the {goal} goal during this window. '
            f'Races: {_races_sentence(season)}')
    if not any(0 in slot['days'] for slot in season['schedule']['slots']):
        text += ' No Sunday evening races.'
    return text


def _short_name(name):
    return re.sub(r'^TTP[0-9]+\s+', '', name, flags=re.IGNORECASE)


def flags_text(season):
    tag = f'TTP{season["number"]}'
    lines = [f'{tag} flagset presets:', f'  !{season["randomCommand"]} -- Random pick from the three {tag} flagsets']
    lines += [f'  !{entry["command"]} -- {_short_name(entry["name"])}' for entry in season['flagsets']]
    return '\n'.join(lines) + '\n'


class SeasonRecord:
    def __init__(self, url, cache_path, built_in):
        self.url = url
        self.cache_path = Path(cache_path) if cache_path else None
        self._built_in = [built_in]
        self._seasons = None

    @property
    def loaded(self):
        """Whether a good copy has ever loaded."""
        return self._seasons is not None

    def _all(self):
        """Newest first."""
        return self._built_in if self._seasons is None else self._seasons

    def for_night(self, night):
        """The season a night falls under: the newest whose first night has come."""
        return next((season for season in self._all() if first_night(season) <= night), None)

    def current(self, when):
        """The season a moment falls under, or the first on file before any has begun."""
        night = when.date() - timedelta(days=1) if when.hour < NIGHT_ROLLOVER_HOUR else when.date()
        return self.for_night(night) or self._all()[-1]

    def goals(self):
        return [season['goal'] for season in self._all()]

    def room_prefixes(self):
        return [season['roomPrefix'] for season in self._all()]

    def by_random_command(self, word):
        """The season a bare !word rolls a random flagset of, or None."""
        form = _reduce(word)
        return next((season for season in self._all() if _reduce(season['randomCommand']) == form), None)

    def oldest_first(self):
        return list(reversed(self._all()))

    def install(self, snapshot):
        """Take a copy already known to be valid."""
        self._seasons = sorted(snapshot['seasons'], key=lambda season: season['number'], reverse=True)

    def load_cache(self):
        """The last good copy from disk, so a restart needs nothing from the network."""
        if not self.cache_path:
            return
        try:
            snapshot = json.loads(self.cache_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning('Season record cache unreadable: %s', exc)
            return
        problems = validate_snapshot(snapshot)
        if problems:
            logger.warning('Season record cache ignored: %s', problems[0])
            return
        self.install(snapshot)

    async def refresh(self, session):
        """Fetch, check whole, then take it. Anything short of that keeps what we have."""
        async with session.get(self.url, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)) as response:
            if response.status != 200:
                raise RuntimeError(f'HTTP {response.status}')
            text = await response.text()
        snapshot = json.loads(text)
        problems = validate_snapshot(snapshot)
        if problems:
            raise ValueError(f'invalid file: {problems[0]}')
        self.install(snapshot)
        self._write_cache(text)

    def _write_cache(self, text):
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.cache_path.parent, prefix=f'.{self.cache_path.name}.')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write(text)
            os.replace(temporary, self.cache_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    async def run(self):
        """Keep the copy fresh for the life of the process. Nothing here can stop the bot."""
        self.load_cache()
        if not self.url:
            logger.info('Season record off: no Z1RR_SEASONS_URL')
            return
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self.refresh(session)
                except Exception as exc:
                    logger.warning('Season record not refreshed; keeping the last good copy: %s', exc)
                await asyncio.sleep(REFRESH_SECONDS)


def _default_cache_path():
    data_dir = (os.environ.get('TTPBOT_DATA_DIR') or '').strip()
    return Path(data_dir) / 'ttp-seasons.json' if data_dir else None


seasons = SeasonRecord(
    (os.environ.get('Z1RR_SEASONS_URL') or '').strip() or None,
    _default_cache_path(),
    BUILT_IN,
)
