"""Record a finished League race on the results form.

Recording a result is the one part of a League race night that has always been
manual: somebody who raced has to remember to fill in a Google Form afterwards.
racetime already knows everything the form asks for, so the bot fills it in.

Two things make this harder than it sounds.

**The form is 1v1 and a co-op race is not.** A co-op room holds four racers,
and the results sheet records it as two rows -- one per pairing -- scored on
summed team times. So one room can produce two submissions, and who raced whom
is not something the room can tell us.

**Only the sheets know the pairings.** The Schedule tab drops a race once it
has happened, so a finished room's pairings come from the Archives tab, which
keeps them. A sheet row belongs to this room when both of its racers are
entrants, which handles co-op without knowing anything about co-op.

Racers are matched by racetime id, never by name: the sheet says "Merks" and
"Sigil" where racetime says "Moneymerks" and "Sigil711", and name matching
silently drops those rows rather than failing loudly.
"""
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp

from ..config import (
    LEAGUE_RESULTS_FIELDS,
    LEAGUE_RESULTS_FORM_URL,
)
from .roster import strip_team_prefix
from .schedule import _parse_start

#: racetime writes a finish as an ISO-8601 duration: "P0DT01H14M39.189022S".
DURATION = re.compile(
    r'^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?'
    r'(?:(?P<minutes>\d+)M)?(?:(?P<seconds>[\d.]+)S)?$'
)

#: A racer who finished. Anything else -- dnf, dq, still racing -- has no time.
FINISHED = 'done'

FETCH_TIMEOUT = aiohttp.ClientTimeout(total=20)
POST_TIMEOUT = aiohttp.ClientTimeout(total=10)

#: A Google Form answers a successful post with 200, or 302 to its own
#: confirmation page. Neither is the 204 the Discord webhooks return.
FORM_ACCEPTED = (200, 302)


def state_key(ended_at, slug, index):
    """The idempotency key for one submission: "<timestamp>|<slug>-<index>".

    racetime writes times with a trailing "Z", which Python cannot read back
    before 3.11 -- and the bot runs on 3.10 in production. The state store
    parses this timestamp when it validates the key, so the offset is spelled
    the way every Python understands.
    """
    stamp = str(ended_at or '').strip()
    if stamp.endswith('Z'):
        stamp = stamp[:-1] + '+00:00'
    # Anything unparseable would be rejected by the store on save, after the
    # form had already been posted -- so it is caught here instead.
    datetime.fromisoformat(stamp)
    return '{}|{}-{}'.format(stamp, slug, index)


class PairingsUnavailable(RuntimeError):
    """The sheets could not be read, so no result can be attributed."""


@dataclass(frozen=True)
class Submission:
    """One row of the results form: who beat whom, and in what time."""

    winner: str
    winner_time: str
    loser: str
    loser_dnf: bool
    loser_time: str

    def form_data(self):
        return {
            LEAGUE_RESULTS_FIELDS['winner']: self.winner,
            LEAGUE_RESULTS_FIELDS['winner_time']: self.winner_time,
            LEAGUE_RESULTS_FIELDS['loser']: self.loser,
            LEAGUE_RESULTS_FIELDS['loser_dnf']: 'Yes' if self.loser_dnf else 'No',
            LEAGUE_RESULTS_FIELDS['loser_time']: '' if self.loser_dnf else self.loser_time,
        }

    def describe(self):
        beaten = 'DNF' if self.loser_dnf else self.loser_time
        return '{} {} beat {} {}'.format(
            self.winner, self.winner_time, self.loser, beaten,
        )


def finish_clock(duration):
    """"P0DT01H14M39.189022S" -> "1:14:39", the shape the form asks for.

    Seconds are truncated rather than rounded, so the form says what the
    racetime results page says.
    """
    match = DURATION.match(str(duration or '').strip())
    if not match:
        return None
    parts = match.groupdict()
    # "PT" is shaped like a duration and says nothing; a race with no time is
    # a racer who did not finish, not a racer who finished instantly.
    if not any(parts.values()):
        return None
    try:
        total = (
            int(parts['days'] or 0) * 86400
            + int(parts['hours'] or 0) * 3600
            + int(parts['minutes'] or 0) * 60
            + int(float(parts['seconds'] or 0))
        )
    except ValueError:
        return None
    return '{}:{:02d}:{:02d}'.format(total // 3600, total % 3600 // 60, total % 60)


def option_for(racer):
    """The exact dropdown option for a racer: "(TML) Merks"."""
    return '({}) {}'.format(racer.team, racer.sheet_name)


def _column_index(header, *names):
    lowered = [str(cell or '').strip().lower() for cell in header]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    return -1


#: How far a sheet row may sit from a room's start and still be that race.
#: Games of one matchup are hours apart, and a room opens half an hour early,
#: so ninety minutes separates them without being brittle about a late start.
NEAR_START = timedelta(minutes=90)


@dataclass(frozen=True)
class Pairing:
    """One scheduled race: two racers, and when they were due to run."""

    one: object
    two: object
    when: object


def pairings_from(rows, roster):
    """Every pairing a schedule-shaped tab lists, with its scheduled time.

    Rows naming anyone the roster does not know are skipped rather than
    guessed at: a pairing we cannot resolve is one we must not submit. A row
    with no readable time is skipped too -- without it the pairing cannot be
    tied to the race it belongs to.
    """
    if not rows:
        return []
    header = rows[0]
    one = _column_index(header, 'runner 1', 'runner one')
    two = _column_index(header, 'runner 2', 'runner two')
    date = _column_index(header, 'date')
    time = _column_index(header, 'time')
    if one < 0 or two < 0 or date < 0 or time < 0:
        return []

    pairings = []
    for row in rows[1:]:
        if max(one, two, date, time) >= len(row):
            continue
        try:
            left = roster.resolve(strip_team_prefix(row[one]))
            right = roster.resolve(strip_team_prefix(row[two]))
            when = _parse_start(row[date], row[time])
        except (ValueError, KeyError):
            continue
        pairings.append(Pairing(one=left, two=right, when=when))
    return pairings


def _started(race_data):
    """When the race went off, as an aware datetime, or None."""
    stamp = str(race_data.get('started_at') or race_data.get('opened_at') or '').strip()
    if not stamp:
        return None
    if stamp.endswith('Z'):
        stamp = stamp[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def entrants_by_racer(race_data, roster):
    """Each entrant, keyed by the roster racer they are.

    Keyed on racetime id where racetime gives one, because the names differ:
    the sheet's "Merks" is racetime's "Moneymerks".
    """
    by_id = {}
    by_name = {}
    for racer in roster:
        if racer.racetime_id:
            by_id[str(racer.racetime_id)] = racer
        # The Twitch name is in here because it is often what racetime shows:
        # the sheet's "Merks" races as "Moneymerks".
        for alias in (racer.sheet_name, racer.display_name, racer.twitch_channel):
            if alias:
                by_name.setdefault(alias.strip().lower(), racer)

    seats = {}
    for entrant in race_data.get('entrants') or []:
        user = entrant.get('user') or {}
        racer = by_id.get(str(user.get('id') or ''))
        if racer is None:
            racer = by_name.get(str(user.get('name') or '').strip().lower())
        if racer is None:
            continue
        seats[racer.sheet_name] = {
            'racer': racer,
            'status': (entrant.get('status') or {}).get('value', ''),
            'time': finish_clock(entrant.get('finish_time')),
        }
    return seats


def _finished(seat):
    return seat['status'] == FINISHED and bool(seat['time'])


def submissions_for(race_data, pairings, roster):
    """The form rows this finished race produces, and anything unresolvable.

    A pairing belongs to this race when both of its racers are entrants *and*
    it was scheduled around the time the race went off. Entrants alone is not
    enough: the same four racers meet again in a later game, and those rows --
    still sitting in Schedule, unplayed -- would otherwise be filed as results
    of this race, with the pairings of a different night.

    With both tests, a co-op room simply matches its two pairings and needs no
    special case.
    """
    started = _started(race_data)
    if started is None:
        return [], ['the race has no start time, so its pairings cannot be found']

    seats = entrants_by_racer(race_data, roster)
    submissions = []
    problems = []
    for pairing in pairings:
        if pairing.when is None or abs(pairing.when - started) > NEAR_START:
            continue
        left, right = pairing.one, pairing.two
        first = seats.get(left.sheet_name)
        second = seats.get(right.sheet_name)
        if first is None or second is None:
            continue
        if not _finished(first) and not _finished(second):
            problems.append(
                'neither {} nor {} finished'.format(left.sheet_name, right.sheet_name)
            )
            continue
        if _finished(first) and _finished(second):
            winner, loser = (
                (first, second) if first['time'] <= second['time'] else (second, first)
            )
        else:
            winner, loser = (first, second) if _finished(first) else (second, first)
        submissions.append(Submission(
            winner=option_for(winner['racer']),
            winner_time=winner['time'],
            loser=option_for(loser['racer']),
            loser_dnf=not _finished(loser),
            loser_time=loser['time'] or '',
        ))
    return submissions, problems


class ResultsRecorder:
    """Posts a finished League race to the results form, once.

    Every submission is recorded in persistent state before the next one is
    attempted, so a crash or a reconnect re-posts nothing: the room's races
    are keyed individually, and a run that fails halfway resumes where it
    stopped rather than starting again.
    """

    def __init__(self, *, roster, store, logger, archives_url, schedule_url,
                 form_url=LEAGUE_RESULTS_FORM_URL, requester=None, fetcher=None):
        self.roster = roster
        self.store = store
        self.logger = logger
        self.archives_url = archives_url
        self.schedule_url = schedule_url
        self.form_url = form_url
        self._requester = requester
        self._fetcher = fetcher

    async def record(self, race_data):
        """Submit every result this race produces. Returns how many were sent."""
        status = (race_data.get('status') or {}).get('value', '')
        if status != 'finished':
            return 0

        slug = str(race_data.get('name') or '').rpartition('/')[2]
        ended = race_data.get('ended_at')
        if not slug or not ended:
            self.logger.error('League result skipped: race has no slug or end time')
            return 0

        try:
            pairings = await self._pairings()
        except PairingsUnavailable:
            self.logger.error(
                'League result skipped for %s: the schedule sheets are unreadable', slug,
            )
            return 0

        submissions, problems = submissions_for(race_data, pairings, self.roster)
        for problem in problems:
            self.logger.error('League result not submitted for %s: %s', slug, problem)
        if not submissions:
            self.logger.warning('League race %s produced no results to submit', slug)
            return 0

        try:
            keys = [state_key(ended, slug, index) for index in range(len(submissions))]
        except ValueError:
            self.logger.error(
                'League result skipped for %s: end time %r cannot be keyed', slug, ended,
            )
            return 0

        sent = 0
        recorded = self.store.load()
        for index, submission in enumerate(submissions):
            key = keys[index]
            if key in recorded:
                continue
            if not await self._post(submission):
                self.logger.error(
                    'League result post failed for %s (%s); the rest will wait',
                    slug, submission.describe(),
                )
                break
            recorded[key] = True
            self.store.save(recorded)
            sent += 1
            self.logger.info('League result recorded: %s', submission.describe())
        return sent

    async def _pairings(self):
        """Every pairing the League has scheduled, played or not.

        Archives holds the races that have happened; Schedule holds the ones
        that have not. A race that finished early enough to still be in
        Schedule is as real as one already archived, so both are read.
        """
        found = []
        failures = 0
        for url in (self.archives_url, self.schedule_url):
            if not url:
                continue
            rows = await self._rows(url)
            if rows is None:
                failures += 1
                continue
            found.extend(pairings_from(rows, self.roster))
        if not found and failures:
            raise PairingsUnavailable('no schedule tab could be read')
        return found

    async def _rows(self, url):
        if self._fetcher is not None:
            return await self._fetcher(url)
        try:
            async with aiohttp.request('get', url, timeout=FETCH_TIMEOUT) as response:
                if response.status != 200:
                    self.logger.error('Schedule tab fetch failed (HTTP %d)', response.status)
                    return None
                text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            self.logger.error('Schedule tab fetch failed safely (%s)', type(exc).__name__)
            return None
        import csv
        import io
        return list(csv.reader(io.StringIO(text)))

    async def _post(self, submission):
        if self._requester is not None:
            return await self._requester(self.form_url, submission.form_data())
        try:
            async with aiohttp.request(
                'post',
                url=self.form_url,
                data=submission.form_data(),
                timeout=POST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status in FORM_ACCEPTED:
                    return True
                self.logger.error('Results form rejected the post (HTTP %d)', response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError) as exc:
            self.logger.error('Results form post failed safely (%s)', type(exc).__name__)
        return False
