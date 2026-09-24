"""Grace minutes: starting scheduled races on time without ejecting the room.

Scheduled TTP races are meant to start on time, with the five-minute grace
period the rules already describe. In practice a handful of repeat latecomers
hold up everyone else, and enforcing it by hand means somebody has to sit in
the room with a sword.

Every racer holds a balance of grace minutes. Being ready at the scheduled
time earns one; making the room wait spends one a minute. The race is force
started once the people holding it up have nothing left to spend, or at the
five-minute mark, whichever comes first. Racetime removes whoever is still not
ready at that moment, which is the point.

Deliberate exceptions:

- **Moderators are outside this entirely.** They neither earn nor spend, and
  if one of them is not ready the bot never force starts: they are in the room
  to exercise judgement, and a bot should not overrule the person holding the
  sword.
- **Never below two ready racers.** Forcing a start that leaves one racer is a
  walkover nobody asked for.
- **A latecomer gets a minute.** Someone who enters after the scheduled time is
  already behind; charging them for the second they arrive reads as a bug.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Dict, List, Optional

#: Everyone starts here, and returns here each season.
GRACE_START = 3
#: Earned minutes stop accumulating here, so nobody banks a season of lateness.
GRACE_CAP = 5
#: Minutes regained per idle day, so a bad night is not a permanent sentence:
#: someone who spent their balance a week ago has earned it back by the time
#: they race again. Deliberately generous -- the ledger is meant to catch
#: repeat lateness, not to punish one late night indefinitely.
GRACE_PER_IDLE_DAY = 1
#: Idle days only restore the starting balance. Anything above it is earned by
#: being ready on time, so waiting a fortnight never buys what punctuality
#: does -- and the people at the cap are there because they turn up on time.
GRACE_REGEN_CAP = GRACE_START
#: The rules doc's grace period, and the longest the bot will ever wait.
MAX_WAIT = timedelta(minutes=5)
#: A racer who joins after the scheduled time is charged from a minute after
#: they arrive rather than from the moment they appear.
LATECOMER_FREE = timedelta(minutes=1)
#: Fewer ready racers than this and a forced start is a walkover, not a race.
MIN_READY_TO_START = 2

READY = 'ready'
#: Statuses that mean "in the room and expected to race". `requested` and
#: `invited` are people who have not joined, and cannot hold a race up.
PRESENT = {'ready', 'not_ready'}


@dataclass(frozen=True)
class Entrant:
    user_id: str
    name: str
    ready: bool
    #: Category moderator, race monitor, or the account that opened the room.
    moderator: bool


@dataclass
class Decision:
    """What the bot should do this tick."""
    messages: List[str] = field(default_factory=list)
    #: Minutes to deduct, by user id.
    spend: Dict[str, int] = field(default_factory=dict)
    #: Users who were ready on time and earn a minute.
    earn: List[str] = field(default_factory=list)
    force_start: bool = False
    #: Why a start the clock called for did not happen, for the log.
    blocked: Optional[str] = None


def entrants_from(race_data, monitors=(), opened_by=None):
    """Read the racetime payload into the handful of fields this needs."""
    monitor_ids = {str(m.get('id')) for m in (monitors or []) if m.get('id')}
    if opened_by and opened_by.get('id'):
        monitor_ids.add(str(opened_by['id']))
    entrants = []
    for raw in race_data.get('entrants') or []:
        user = raw.get('user') or {}
        user_id = str(user.get('id') or '')
        status = (raw.get('status') or {}).get('value', '')
        if not user_id or status not in PRESENT:
            continue
        entrants.append(Entrant(
            user_id=user_id,
            name=user.get('name') or user_id,
            ready=status == READY,
            moderator=bool(user.get('can_moderate')) or user_id in monitor_ids,
        ))
    return entrants


class GraceLedger:
    """Balances by racetime user id, wiped when the season changes."""

    def __init__(self, path, season, logger=None):
        self.path = Path(path)
        self.season = season
        self.logger = logger
        self.balances = {}
        self.names = {}
        #: user id -> the day their balance was last brought up to date.
        self.accrued = {}
        self._accrued_on = None
        self._load()

    def _load(self):
        try:
            document = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # A ledger nobody can read is not worth stopping races for: start
            # the season again rather than crash every room.
            if self.logger:
                self.logger.error('Grace ledger unreadable; starting fresh', exc_info=True)
            return
        if not isinstance(document, dict) or document.get('season') != self.season:
            return
        balances = document.get('balances')
        if isinstance(balances, dict):
            self.balances = {
                str(k): max(0, min(GRACE_CAP, int(v)))
                for k, v in balances.items() if isinstance(v, int)
            }
        names = document.get('names')
        if isinstance(names, dict):
            self.names = {str(k): str(v) for k, v in names.items()}
        accrued = document.get('accrued')
        if isinstance(accrued, dict):
            self.accrued = {str(k): str(v) for k, v in accrued.items()}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({
            'season': self.season,
            'balances': self.balances,
            'names': self.names,
            'accrued': self.accrued,
        }, indent=2, sort_keys=True), encoding='utf-8')
        temporary.replace(self.path)

    def balance(self, user_id):
        return self.balances.get(str(user_id), GRACE_START)

    def accrue(self, now):
        """Grant a minute per whole idle day, up to the starting balance.

        Only racers who already hold a balance accrue: someone who has never
        spent or earned is on the starting balance anyway, and inventing an
        entry for them would fill the ledger with people who have done nothing.
        """
        today = now.date() if hasattr(now, 'date') else now
        changed = False
        for user_id, balance in list(self.balances.items()):
            last = self.accrued.get(user_id)
            if last is not None:
                try:
                    days = (today - date.fromisoformat(last)).days
                except ValueError:
                    days = 0
                if days > 0 and balance < GRACE_REGEN_CAP:
                    self.balances[user_id] = min(
                        GRACE_REGEN_CAP, balance + days * GRACE_PER_IDLE_DAY,
                    )
            if last != today.isoformat():
                self.accrued[user_id] = today.isoformat()
                changed = True
        self._accrued_on = today.isoformat()
        if changed:
            self.save()

    def apply(self, decision, entrants=()):
        """Apply a decision's spends and earnings, and remember the names."""
        for entrant in entrants:
            self.names[entrant.user_id] = entrant.name
        for user_id, minutes in decision.spend.items():
            self.balances[str(user_id)] = max(0, self.balance(user_id) - minutes)
        for user_id in decision.earn:
            self.balances[str(user_id)] = min(GRACE_CAP, self.balance(user_id) + 1)
        # A balance created today is already up to date; without this it would
        # be treated as never accrued and lose its first idle day.
        if self._accrued_on is not None:
            for user_id in list(decision.spend) + list(decision.earn):
                self.accrued.setdefault(str(user_id), self._accrued_on)
        if decision.spend or decision.earn:
            self.save()


class GraceRace:
    """One scheduled race's countdown. Tick it; it decides.

    Kept as an object rather than a function because two things have to be
    remembered across ticks: when each racer first appeared (so a latecomer is
    charged from their own arrival) and how much each has already been charged
    (so a 15-second tick does not bill them four times a minute).
    """

    def __init__(self, scheduled, ledger, enforce=False):
        self.scheduled = scheduled
        self.ledger = ledger
        self.enforce = enforce
        self.first_seen = {}
        self.charged = {}
        self.accrued_this_race = False
        self.earned = False
        self.announced = False
        self.finished = False

    def _charge_from(self, entrant):
        """When this entrant's minutes start being spent."""
        seen = self.first_seen.get(entrant.user_id, self.scheduled)
        if seen <= self.scheduled:
            return self.scheduled
        return seen + LATECOMER_FREE

    def tick(self, now, entrants):
        # Bring balances up to date before anything reads or reports them, so
        # the room is told what people actually hold right now.
        if not self.accrued_this_race:
            self.accrued_this_race = True
            self.ledger.accrue(now)
        decision = Decision()
        if self.finished or now < self.scheduled:
            for entrant in entrants:
                self.first_seen.setdefault(entrant.user_id, now)
            return decision
        for entrant in entrants:
            self.first_seen.setdefault(entrant.user_id, now)

        ready = [e for e in entrants if e.ready]
        unready = [e for e in entrants if not e.ready]
        charged_now = [e for e in unready if not e.moderator]

        if not self.earned:
            # Earned once, at the scheduled time, by whoever was ready then.
            self.earned = True
            decision.earn = [e.user_id for e in ready if not e.moderator]

        if not unready:
            self.finished = True
            return decision

        for entrant in charged_now:
            # Charged against the clock, not the balance: the ledger floors at
            # zero, and clamping here would stop the last minute being spent.
            owed = max(0, int((now - self._charge_from(entrant)) // timedelta(minutes=1)))
            already = self.charged.get(entrant.user_id, 0)
            if owed > already:
                decision.spend[entrant.user_id] = owed - already
                self.charged[entrant.user_id] = owed

        out_of_grace = [
            e for e in charged_now
            if self.ledger.balance(e.user_id) - decision.spend.get(e.user_id, 0) <= 0
        ]
        deadline_reached = now >= self.scheduled + MAX_WAIT
        wants_start = deadline_reached or len(out_of_grace) == len(charged_now)

        if not self.announced:
            self.announced = True
            decision.messages.append(self._opening_message(unready, deadline_reached))

        if not wants_start:
            return decision

        moderator_waiting = [e for e in unready if e.moderator]
        if moderator_waiting:
            decision.blocked = 'moderator not ready: {}'.format(
                ', '.join(e.name for e in moderator_waiting))
            self.finished = True
            decision.messages.append(
                'Not force starting: {} is a race monitor and is not ready. '
                'Over to you.'.format(moderator_waiting[0].name))
            return decision

        if len(ready) < MIN_READY_TO_START:
            decision.blocked = 'only {} ready racer(s)'.format(len(ready))
            self.finished = True
            decision.messages.append(
                'Not force starting: that would leave fewer than two racers. '
                'A monitor can start or cancel this one.')
            return decision

        self.finished = True
        names = ', '.join(e.name for e in charged_now)
        if self.enforce:
            decision.force_start = True
            decision.messages.append(
                'Force starting. Out of grace minutes and removed from the race: {}.'.format(names))
        else:
            decision.messages.append(
                'Trial run: from the week of September 28 this race would have force '
                'started now, removing {}. Nobody is removed today.'.format(names))
        return decision

    def _opening_message(self, unready, deadline_reached):
        parts = []
        for entrant in unready:
            if entrant.moderator:
                parts.append('{} (monitor)'.format(entrant.name))
            else:
                parts.append('{} ({} grace)'.format(entrant.name, self.ledger.balance(entrant.user_id)))
        # %-I is not portable (Windows), and this runs in tests there.
        latest = (self.scheduled + MAX_WAIT).strftime('%I:%M %p').lstrip('0')
        return ('Scheduled start time. Not ready: {}. Ready up or the race force starts '
                'when those minutes run out, and no later than {} ET.'.format(
                    ', '.join(parts), latest) if not deadline_reached else
                'Scheduled start time has passed. Not ready: {}.'.format(', '.join(parts)))
