"""The Autumn Tournament's race night.

The same shape as the League's -- read a schedule, wake a booth at T-35, open a
room at T-30 -- over a different notion of what a race *is*.

A League race is a fixture: it has a week, two teams, and a start time the sheet
owns outright. An Autumn race is a match in a bracket, and that difference
removes the League's hardest bug rather than reproducing it. The League keys its
room state by start time, so a postponed race is a cache miss and gets a second
room; it works around that by matching on a slug and re-keying. Here the key is
the match id, which does not move when the time does. A reschedule is the same
match at a different hour, and the room that already exists for it is found by
asking about the match.

What this does not decide:

  * *when* a race is. The Schedule tab owns that. The engine's `times` is a
    mirror, written after the sheet says so, and never read back as a source.
  * *whether* a match can be raced at all. The engine owns that, and it says so
    by refusing the mirror: `time` refuses a match that is not in the bracket,
    one that is a bye, and one that has already been raced and won. So a refusal
    holds the match rather than being logged past.
  * *which* match a row is about. `matching.py` answers that against the
    bracket, and this persists the answer so a row keeps the match it was first
    given -- otherwise a played final turns its own row into the reset's.
  * who won, or who advances. A runner that could award a match would be a
    second set of hands on the tournament during a race night.

Everything that can be done twice is guarded by something that survives a
restart, and everything that might not have happened is retried. Those are
different questions and they need different records, which is why creating a
room, waking a booth and posting an announcement each have their own.
"""

import asyncio
from uuid import uuid4
from datetime import datetime, timedelta

from ..config import TIMEZONE
from ..state import UNCERTAIN_RACE
from .engine import NOT_RECORDED
from .matching import canonical, match_rows, row_key

#: One minute, as the League ticks. Everything below is a window rather than an
#: instant, so a missed tick costs nothing.
TICK_SECONDS = 60

#: The booth needs waking before the room opens, because a cold control plane
#: takes a few minutes to come up and nobody wants to watch it do that.
BOOTH_WAKE_MINUTES_BEFORE = 35

#: When the room opens. Thirty minutes is what the League settled on: long
#: enough to sort out a seed and a stream, short enough that nobody forgets.
ROOM_OPEN_MINUTES_BEFORE = 30

#: How late a race may be picked up. Past this the start has been and gone, and
#: opening a room for it is worse than leaving it to an admin.
ROOM_OPEN_GRACE = timedelta(hours=2)


class AutumnScheduler:
    """Ticks the tournament.

    Collaborators are injected rather than constructed, and every one is
    optional. A relay with no engine token, no schedule URL or no room opener
    runs the League and says nothing about Autumn -- which is what "not
    configured" should look like, rather than an exception a minute.
    """

    def __init__(self, source, engine, logger, bindings_store=None,
                 created_store=None, mirrored_store=None, announced_store=None,
                 open_room=None, wake_booth=None, announce=None, invite=None,
                 event='autumn', request_booth=None, booth_notice_store=None,
                 announce_continuation=None, recover_results=None, recover_room=None):
        self.source = source
        self.engine = engine
        self.logger = logger
        self.event = event

        self.bindings_store = bindings_store
        self.created_store = created_store
        self.mirrored_store = mirrored_store
        self.announced_store = announced_store
        self.booth_notice_store = booth_notice_store

        # Each of these is "ask somebody else to do the side effect", so this
        # class can be ticked in a test without a racetime account or a Discord
        # token. An opener returns a URL, `UNCERTAIN_RACE` when it created
        # something whose answer was lost, or None when it definitely did not --
        # the League's contract, and the distinction the whole room path turns on.
        self._open_room = open_room
        self._wake_booth = wake_booth
        self._request_booth = request_booth
        self._announce = announce
        self._announce_continuation = announce_continuation
        self._invite = invite
        self._recover_results = recover_results
        self._recover_room = recover_room
        self._workflow = False
        self._workflow_document = None

        self.bindings = self._load(bindings_store)
        self.created = self._load(created_store)
        self.mirrored = self._load(mirrored_store)
        self.announced = self._load(announced_store)
        self.booth_notices = self._load(booth_notice_store)

        #: Matches whose booth is awake. In memory on purpose: waking a control
        #: plane twice is harmless -- it is already awake -- and a note that
        #: survived a restart would skip the wake after the restart that most
        #: needs it.
        self._woken = set()
        #: Matches whose room is uncertain and has been complained about, so the
        #: complaint is once rather than once a minute.
        self._flagged = set()
        #: No record of who has been invited is kept here at all. The handler
        #: holds that, in the room's own state, because it is the thing that
        #: knows who is already an entrant.

        #: Set once state could not be read or written. Nothing is attempted
        #: while it holds: acting on state we know is missing is how a second
        #: room gets made for a race that already has one.
        self.stopped = any(
            store is not None and loaded is None
            for store, loaded in (
                (bindings_store, self.bindings),
                (created_store, self.created),
                (mirrored_store, self.mirrored),
                (announced_store, self.announced),
                (booth_notice_store, self.booth_notices),
            )
        )

    @property
    def configured(self):
        return bool(self.engine) and bool(self.source) and self.source.configured

    def _load(self, store):
        """A store's entries, or None when they could not be read.

        None rather than {}, because those mean opposite things. An unreadable
        store is state we *had* and lost, and the point of the marker the store
        leaves behind is that this cannot be mistaken for a fresh start.
        """
        if store is None:
            return {}
        try:
            return store.load()
        except Exception:
            self.logger.error(
                'Autumn state could not be read; the tournament runner is stopped '
                'until it is recovered', exc_info=True)
            return None

    def _save(self, store, entries):
        """Persist, and say whether it worked.

        The return value is load-bearing and every caller checks it. Setting a
        flag and carrying on was not enough: the tick that failed to save went on
        to open a room anyway, and the next restart had no record of it.
        """
        if store is None:
            return True
        try:
            store.save(entries)
            return True
        except Exception:
            self.stopped = True
            self.logger.error(
                'Autumn state could not be saved; the tournament runner is '
                'stopped', exc_info=True)
            return False

    # -- keys --------------------------------------------------------------

    def _match_key(self, match_id, game=1):
        if self._workflow:
            return '{}-{}|{}|{}'.format(self.event, self.engine.edition, match_id, game)
        return '{}|{}'.format(self.event, match_id)

    # -- the tick ----------------------------------------------------------

    async def run(self):
        """Tick forever. Never let the tournament kill the process."""
        while True:
            try:
                await self.tick(self._now())
            except Exception:
                self.logger.error('Error in Autumn scheduler', exc_info=True)
            if self._recover_results is not None:
                try:
                    await self._recover_results()
                except Exception:
                    self.logger.error('Autumn result recovery failed; receipts preserved', exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    @staticmethod
    def _now():
        return datetime.now(TIMEZONE)

    async def tick(self, now):
        if not self.configured or self.stopped:
            return

        # The bracket first. Without it a row cannot be resolved, and a row
        # resolved against a bracket we could not read would be a guess.
        try:
            drawn = await self.engine.draw()
        except Exception as exc:
            self.logger.error('Autumn: the draw could not be read: %s', exc)
            return
        if not drawn.get('drawn'):
            return

        self._workflow = drawn.get('workflowVersion') == 2
        if self._workflow:
            try:
                self._workflow_document = (await self.engine.state()).get('document') or {}
                if (drawn.get('edition') != self.engine.edition
                        or self._workflow_document.get('edition') != self.engine.edition):
                    self.logger.error('Autumn: configured edition does not own this draw; runner held')
                    return
            except Exception:
                self.logger.error('Autumn: cannot verify workflow state; runner held', exc_info=True)
                return
        matches = {match['id']: match for match in drawn.get('matches', [])}
        aliases = drawn.get('aliases') or {}

        schedule = await self.source.rows(now)
        if not schedule.readable:
            # Said by the source already, once. Nothing is opened on a schedule
            # nobody can read -- including nothing *closed*, which is why this is
            # a return and not an empty list.
            return

        matching = match_rows(
            [row.as_row() for row in schedule.in_time_order()],
            matches,
            event=self.event,
            aliases=aliases,
            bindings=self._unscoped_bindings(),
            edition=self.engine.edition if self._workflow else None,
        )

        if not self._remember(matching.bindings):
            return
        self._report(matching.unresolved, schedule)
        self._schedule_observed_at = schedule.observed_at or now
        if self._workflow and not schedule.bad and not matching.unresolved:
            if not await self._cancel_missing(matching.resolved):
                return

        # The crew columns belong to the row, not to the match, so they are
        # carried across by time and pair rather than looked up again.
        #
        # Keyed by the *canonical* names, because that is what a resolved race
        # carries. Keying by the sheet's spelling looked right and silently lost
        # the channel for every row with a ranking prefix -- `(46) ISUMatt` does
        # not flatten to `ISUMatt` -- so no booth was ever woken for a row that
        # came from the form's dropdowns, which is all of them.
        crew_for = {
            row_key(
                row.at,
                canonical(row.runner_one, aliases),
                canonical(row.runner_two, aliases),
            ) + '|{}'.format(row.game or 1): row
            for row in schedule.rows
        }

        for race in matching.resolved:
            if self.stopped:
                return
            try:
                await self._handle(race, crew_for.get(
                    row_key(race.at, race.runner_one, race.runner_two) + '|{}'.format(race.identity.game)), now)
            except Exception:
                self.logger.error(
                    'Error handling Autumn %s', race.match_id, exc_info=True)

    async def _handle(self, race, row, now):
        """One resolved race, at whatever stage it is at."""
        if self._workflow and race.status == 'cancelled':
            await self.engine.cancel_time(race.match_id, race.identity.game, self.engine.edition, self._schedule_observed_at.isoformat())
            return
        if not self._workflow and race.identity.game != 1:
            self.logger.error('Autumn: per-game rooms require the migrated engine')
            return
        # The mirror first, because the engine's answer to it is also the
        # engine's answer to "may this be raced at all".
        if not await self._mirror(race, now):
            return

        if race.conditional:
            # Scheduled but not raceable: the grand-final reset before the final
            # has been played, or a match whose racers are not both known. A time
            # is still mirrored -- the finalists agreed it -- but no room opens.
            return

        opens_at = race.at - timedelta(minutes=ROOM_OPEN_MINUTES_BEFORE)
        wakes_at = race.at - timedelta(minutes=BOOTH_WAKE_MINUTES_BEFORE)
        too_late = race.at + ROOM_OPEN_GRACE

        if now > too_late:
            # The start has been and gone. An admin can still make a room; a
            # scheduler that does it hours late is just confusing.
            return

        if self._workflow:
            if now < wakes_at:
                return
            work = await self._room_work(race)
            if work is None:
                return
            if work.get('mayWake'):
                await self._wake(race, row)
            url = await self._room_v2(race, row, work)
        else:
            if now >= wakes_at:
                await self._wake(race, row)
            if now < opens_at:
                return
            url = await self._room(race, row)
        if not url:
            return

        # Invites first. A racer who cannot get into a room they can see is the
        # one failure here that stops a race rather than inconveniencing it.
        await self._let_in(race, url)

        # Then the announcement, every tick and behind its own guard. An
        # announcement that failed once is a match nobody was told about, and
        # tying it to the creation meant it was never tried again.
        booth = None
        if self._request_booth is not None:
            try:
                booth = await self._request_booth(race, row, url)
            except Exception:
                self.logger.error('Autumn: booth request failed for %s; will retry',
                                  race.match_id, exc_info=True)
        await self._tell(race, row, url, booth)

    # -- the mirror --------------------------------------------------------

    async def _mirror(self, race, now=None):
        """Tell the engine when this match is. Returns whether to carry on.

        Keyed by the match, so a reschedule is a *changed* value rather than a
        second entry -- which is the whole reason the key is not the time.

        A refusal stops the race here. The engine refuses a `time` for exactly
        three reasons and every one of them means no room should open: the match
        is not in the bracket, it is a bye, or it has already been raced and won.
        The sheet owns *when* a match is; it does not get to say that a match
        which is over is happening tonight.
        """
        key = self._match_key(race.match_id, race.identity.game)
        when = race.at.isoformat()
        if not self._workflow and self.mirrored.get(key) == when:
            return True

        if self._workflow:
            written = await self.engine.mirror_time(race.match_id, race.at, game=race.identity.game,
                edition=self.engine.edition, observed_at=self._schedule_observed_at.isoformat())
        else:
            written = await self.engine.mirror_time(race.match_id, race.at)
        if written.ok:
            self.mirrored[key] = when
            return self._save(self.mirrored_store, self.mirrored)

        if written.outcome == NOT_RECORDED:
            self.logger.error(
                'Autumn: the engine refused the time for %s (%s), so no room is '
                'opened for it; the bracket disagrees that this is raceable',
                race.match_id, written.detail)
            return False

        # Nobody knows. The local note is deliberately *not* updated, so the next
        # tick sends it again -- safe because the write is idempotent, and
        # necessary because an unconfirmed write may never have landed. The room
        # is not held up for it: the sheet owns the time, and a bookkeeping
        # answer that went missing is not a reason to leave two racers without a
        # room. Eligibility is re-checked from the draw every tick regardless.
        self.logger.warning(
            'Autumn: the engine has not confirmed %s at %s (%s: %s)',
            race.match_id, when, written.outcome, written.detail)
        return not self._workflow

    # -- the side effects --------------------------------------------------

    async def _wake(self, race, row):
        """Ask the booth to wake, once it has actually woken.

        Marked done *after* the call rather than before. Before meant a single
        transient failure became a permanent omission: the flag said it had been
        asked, and no later tick asked again.
        """
        if self._wake_booth is None or self._match_key(race.match_id, race.identity.game) in self._woken:
            return
        channel = getattr(row, 'channel', '') if row else ''
        if not channel:
            # No channel on the row means no restream, which is most matches.
            return
        try:
            await self._wake_booth(race, channel)
        except Exception:
            # A booth that will not wake costs a restream, not a race, and the
            # next tick tries again.
            self.logger.error(
                'Autumn: could not wake the booth for %s; will try again',
                race.match_id, exc_info=True)
            return
        self._woken.add(self._match_key(race.match_id, race.identity.game))

    async def _cancel_missing(self, races):
        active = {(race.match_id, race.identity.game) for race in races if race.status == 'scheduled'}
        document = self._workflow_document
        for match_id, games in document.get('gameTimes', {}).items():
            played = {game['game'] for game in document.get('games', {}).get(match_id, [])}
            for number, scheduled in games.items():
                game = int(number)
                if (scheduled.get('source') != 'sheet' or scheduled.get('status') != 'scheduled'
                        or game in played or (match_id, game) in active):
                    continue
                result = await self.engine.cancel_time(match_id, game, self.engine.edition, self._schedule_observed_at.isoformat())
                if not result.ok:
                    self.logger.error('Autumn: cannot confirm removed schedule row; room work held')
                    return False
        return True

    def _adopt_legacy(self, race):
        # Only the known migration edition may inherit edition-less receipts.
        if self.engine.edition != '2026' or race.identity.game != 1:
            return True
        old = '{}|{}'.format(self.event, race.match_id)
        new = self._match_key(race.match_id, 1)
        for entries, store in [(self.created,self.created_store),(self.mirrored,self.mirrored_store),
                               (self.announced,self.announced_store),(self.booth_notices,self.booth_notice_store)]:
            if old not in entries:
                continue
            if new in entries:
                # A newer engine-owned uncertain marker is not overwritten by
                # an old URL; engine receipts decide whether it can be used.
                continue
            entries[new] = entries[old]
            if not self._save(store, entries):
                return False
        return True

    def _room_identity(self, race):
        return dict(edition=self.engine.edition, matchId=race.match_id, game=race.identity.game)

    async def _room_work(self, race):
        if not self._adopt_legacy(race):
            return None
        identity = self._room_identity(race)
        existing = self.created.get(self._match_key(race.match_id, race.identity.game))
        if existing:
            if existing == UNCERTAIN_RACE:
                # An engine-owned claim is already authoritative. Do not add a
                # second legacy marker while importing our own last attempt.
                actions = self._workflow_document.get('actions', {}).values()
                if not any(a.get('kind') == 'race-room' and a.get('matchId') == race.match_id
                           and a.get('game') == race.identity.game for a in actions):
                    imported = await self.engine.import_room(dict(identity, uncertain=True))
                    if not imported.ok:
                        return None
            else:
                ids = self._workflow_document.get('racetimeIds', {})
                imported = await self.engine.import_room(dict(identity, room=existing,
                    racers={race.runner_one: ids.get(race.runner_one), race.runner_two: ids.get(race.runner_two)}))
                if not imported.ok:
                    return None
        result = await self.engine.room_work(dict(identity, at=race.at.isoformat()))
        if not result.ok:
            self.logger.error('Autumn: room authority could not be confirmed for %s: %s', race.match_id, result.detail)
            return None
        return result.answer

    async def _room_v2(self, race, row, work):
        room = work.get('room')
        if room:
            return None if room.get('obsolete') else room['room']
        action = work.get('action') or {}
        race.room_marker = action.get('marker')
        identity = self._room_identity(race)
        if action.get('status') in ('claimed', 'uncertain', 'withdrawn') and action.get('claim'):
            # Expiry is permission to recover, never to make another room.
            if self._recover_room is None:
                return None
            recovered = await self._recover_room(race, action)
            if not recovered:
                return None
            receipt = await self.engine.complete_room(dict(identity, actionId=action['id'],
                claim=action['claim'], status='created', room=recovered))
            return recovered if receipt.ok and receipt.answer.get('usable') else None
        if not work.get('mayOpen') or self._open_room is None:
            return None
        claim = await self.engine.claim_room(dict(identity, actionId=action['id'],requestId=uuid4().hex,by='ttpbot'))
        if not claim.ok:
            return None
        action = claim.answer['action']
        race.room_marker = action['marker']
        key = self._match_key(race.match_id, race.identity.game)
        self.created[key] = UNCERTAIN_RACE
        if not self._save(self.created_store,self.created):
            return None
        try:
            made = await self._open_room(race,row)
        except Exception:
            made = UNCERTAIN_RACE
            self.logger.error('Autumn: room request uncertain; claim retained',exc_info=True)
        status = 'uncertain' if made == UNCERTAIN_RACE else 'created' if made else 'not-created'
        receipt = await self.engine.complete_room(dict(identity,actionId=action['id'],claim=action['claim'],
            status=status,room=made if status=='created' else None,
            reason='opener verified rejection before creation' if status=='not-created' else None))
        if not receipt.ok:
            return None
        if status=='not-created':
            self.created.pop(key,None);self._save(self.created_store,self.created)
        if not receipt.answer.get('usable'):
            return None
        self.created[key]=made
        return made if self._save(self.created_store,self.created) else None

    async def _room(self, race, row):
        """The room for this match, making it if there is not one yet.

        Reserved before it is created, which is the whole point. Creating first
        and recording afterwards means a creation whose answer is lost leaves no
        trace, and the next tick makes a second room -- the one mistake a race
        night cannot absorb. So the reservation goes to disk first, and a
        creation that does not come back cleanly stays a reservation rather than
        becoming nothing.
        """
        key = self._match_key(race.match_id, race.identity.game)
        existing = self.created.get(key)

        if existing is None:
            if self._open_room is None:
                return None

            self.created[key] = UNCERTAIN_RACE
            if not self._save(self.created_store, self.created):
                # Not created. Persisting the intent is the precondition for
                # attempting it, so without that there is no attempt.
                del self.created[key]
                return None

            try:
                made = await self._open_room(race, row)
            except Exception:
                # A raise is exactly "we do not know". The reservation stays, and
                # nothing tries again on its own.
                self.logger.error(
                    'Autumn: opening the room for %s did not come back; it is '
                    'recorded as uncertain and will not be retried',
                    race.match_id, exc_info=True)
                return None

            if not made:
                # The opener's contract: falsey means it definitely did not
                # create anything, so the reservation is released and a later
                # tick may try again.
                del self.created[key]
                self._save(self.created_store, self.created)
                return None

            self.created[key] = made
            if not self._save(self.created_store, self.created):
                return None
            existing = made

        if existing == UNCERTAIN_RACE:
            # A room may exist under a name we never learned. Never create
            # another; somebody looks, and either fills it in or clears it.
            if race.match_id not in self._flagged:
                self._flagged.add(race.match_id)
                self.logger.error(
                    'Autumn: %s has a room that was created but never confirmed. '
                    'No second room will be opened. Find it on racetime and '
                    'record it, or clear the entry if there is none.',
                    race.match_id)
            return None

        return existing

    async def _let_in(self, race, url):
        """Tell the racetime handler who belongs in this room.

        Not "invite them": the invite itself is sent over the room's websocket by
        the handler, which is the only thing holding that socket. This writes the
        list where the handler reads it, and the handler owns the once-only guard.

        Done on *every* tick a race is in its window rather than once at creation.
        Handler state does not survive a process restart, and the handler has no
        title fallback for a tournament -- so re-seeding is the recovery, and it
        keeps the scheduler's list authoritative.
        """
        if self._invite is None:
            return
        try:
            await self._invite(race, url)
        except Exception:
            self.logger.error(
                'Autumn: could not tell the handler who to invite to %s',
                race.match_id, exc_info=True)

    async def _tell(self, race, row, url, booth=None):
        """Announce once; recover a late already-on-air warning separately."""
        if self._workflow:
            await self._queue_notice(race, row, url, booth)
            return
        if self._announce is None:
            return
        key = self._match_key(race.match_id, race.identity.game)
        continuation = bool(booth and booth.is_continuation)
        if self.announced.get(key):
            if not continuation or self.booth_notices.get(key) or self._announce_continuation is None:
                return
            try:
                await self._announce_continuation(race, row, url)
            except Exception:
                self.logger.error('Autumn: could not announce continuation for %s; will retry',
                                  race.match_id, exc_info=True)
                return
        else:
            try:
                if booth is None:
                    await self._announce(race, row, url)
                else:
                    await self._announce(race, row, url, booth=booth)
            except Exception:
                self.logger.error('Autumn: could not announce %s; will try again',
                                  race.match_id, exc_info=True)
                return
            self.announced[key] = True
            if not self._save(self.announced_store, self.announced):
                return
        if continuation:
            self.booth_notices[key] = True
            self._save(self.booth_notice_store, self.booth_notices)

    async def _queue_notice(self, race, row, url, booth):
        key = self._match_key(race.match_id, race.identity.game)
        continuation = bool(booth and booth.is_continuation)
        if self.announced.get(key):
            if not continuation or self.booth_notices.get(key):
                return
            phase = 'continuation'
        else:
            phase = 'room'
        try:
            answer = await self.engine.queue_announcement(dict(
                edition=self.engine.edition, matchId=race.match_id, game=race.identity.game,
                room=url, phase=phase, continuation=continuation,
                crew=list(getattr(row, 'crew', ()) or ())))
            if not answer.ok:
                self.logger.error('Autumn: announcement queue unconfirmed for %s; no Discord post attempted', race.match_id)
                return
        except Exception:
            self.logger.error('Autumn: cannot queue announcement for %s', race.match_id, exc_info=True)
            return
        if phase == 'room':
            self.announced[key] = True
            if not self._save(self.announced_store, self.announced):
                return
        if continuation:
            self.booth_notices[key] = True
            self._save(self.booth_notice_store, self.booth_notices)

    # -- bookkeeping -------------------------------------------------------

    def _unscoped_bindings(self):
        """The bindings as the matcher wants them: row key -> match id.

        Stored with the competition in front so two tournaments cannot read each
        other's, and handed over without it because the matcher only ever deals
        with one.
        """
        prefixes = ['{}|'.format(self.event)]
        if self._workflow:
            prefixes = (prefixes if self.engine.edition == '2026' else []) + ['{}-{}|'.format(self.event, self.engine.edition)]
        return {key[len(prefix):]: value for prefix in prefixes
                for key, value in self.bindings.items() if key.startswith(prefix)}

    def _remember(self, bindings):
        """Persist any new binding. Returns whether it is safe to carry on.

        Nothing is done on a tick whose bindings could not be saved. A room
        opened against a binding that only exists in memory is a room the next
        restart cannot account for.
        """
        added = False
        for key, match_id in bindings.items():
            scope = '{}-{}'.format(self.event, self.engine.edition) if self._workflow else self.event
            scoped = '{}|{}'.format(scope, key)
            if self.bindings.get(scoped) == match_id:
                continue
            # A row never changes the match it was given. If this ever fires it
            # is a bug upstream, and overwriting quietly would hide it.
            if scoped in self.bindings:
                self.logger.error(
                    'Autumn: row %s was bound to %s and is now %s; keeping the '
                    'first', key, self.bindings[scoped], match_id)
                continue
            self.bindings[scoped] = match_id
            added = True
        if not added:
            return True
        return self._save(self.bindings_store, self.bindings)

    def _report(self, unresolved, schedule):
        """Say what could not be placed. Never silently.

        A row nobody can place is two racers who think they are scheduled, and
        the first anyone hears of it otherwise is on the night.
        """
        for row in schedule.bad:
            self.logger.warning(
                'Autumn schedule row %d could not be read: %s', row.line, row.reason)
        for miss in unresolved:
            self.logger.warning(
                'Autumn: no match for %s vs %s at %s -- %s',
                miss.runner_one, miss.runner_two,
                miss.at.isoformat() if hasattr(miss.at, 'isoformat') else miss.at,
                miss.reason)
