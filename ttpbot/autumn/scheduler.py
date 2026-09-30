"""The Autumn Tournament's race night.

The same shape as the League's -- read a schedule, wake a booth at T-35, open a
room at T-30 -- over a different notion of what a race *is*.

A League race is a fixture: it has a week, two teams, and a start time that the
sheet owns outright. An Autumn race is a match in a bracket, and that difference
removes the League's hardest bug rather than reproducing it. The League keys its
room state by start time, so a postponed race is a cache miss and gets a second
room; it works around that by matching on a slug and re-keying. Here the key is
the match id, which does not move when the time does. A reschedule is the same
match at a different hour, and the room that already exists for it is found by
asking about the match.

What this does not decide:

  * *when* a race is. The Schedule tab owns that. The engine's `times` is a
    mirror, written after the sheet says so, and never read back as a source.
  * *which* match a row is about. `matching.py` answers that against the
    bracket, and this persists the answer so a row keeps the match it was first
    given -- otherwise a played final turns its own row into the reset's.
  * who won, or who advances. The engine owns those, and nothing here writes
    them. A runner that could award a match is a second set of hands on the
    tournament during a race night.
"""

import asyncio
from datetime import datetime, timedelta

from ..config import TIMEZONE
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

    Collaborators are injected rather than constructed, and every one of them is
    optional. A relay with no engine token, no schedule URL or no room opener
    runs the League and says nothing about Autumn -- which is what "not
    configured" should look like, rather than an exception a minute.
    """

    def __init__(self, source, engine, logger, bindings_store=None,
                 created_store=None, mirrored_store=None,
                 open_room=None, wake_booth=None, announce=None,
                 event='autumn'):
        self.source = source
        self.engine = engine
        self.logger = logger
        self.event = event

        self.bindings_store = bindings_store
        self.created_store = created_store
        self.mirrored_store = mirrored_store

        # Each of these is "ask somebody else to do the side effect", so this
        # class can be ticked in a test without a racetime account or a Discord
        # token.
        self._open_room = open_room
        self._wake_booth = wake_booth
        self._announce = announce

        self.bindings = self._load(bindings_store)
        self.created = self._load(created_store)
        self.mirrored = self._load(mirrored_store)

        #: Matches whose booth has been asked to wake, this process.
        self._woken = set()
        #: True once the state could not be loaded. Nothing is attempted while it
        #: is set: acting on state we know is missing is how a second room gets
        #: made for a race that already has one.
        self.stopped = False
        if any(store is not None and loaded is None for store, loaded in (
            (bindings_store, self.bindings),
            (created_store, self.created),
            (mirrored_store, self.mirrored),
        )):
            self.stopped = True

    @property
    def configured(self):
        return bool(self.engine) and bool(self.source) and self.source.configured

    def _load(self, store):
        """A store's entries, or None when they could not be read.

        None rather than {}, because those mean opposite things. An unreadable
        store is state we *had* and lost, and the whole point of the marker the
        store leaves is that this cannot be mistaken for a fresh start.
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

    # -- keys --------------------------------------------------------------

    def _match_key(self, match_id):
        return '{}|{}'.format(self.event, match_id)

    def _row_key(self, race):
        return '{}|{}'.format(
            self.event, row_key(race.at, race.runner_one, race.runner_two))

    # -- the tick ----------------------------------------------------------

    async def run(self):
        """Tick forever. Never let the tournament kill the process."""
        while True:
            try:
                await self.tick(self._now())
            except Exception:
                self.logger.error('Error in Autumn scheduler', exc_info=True)
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
        )

        self._remember(matching.bindings)
        self._report(matching.unresolved, schedule)

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
            ): row
            for row in schedule.rows
        }

        for race in matching.resolved:
            try:
                await self._handle(race, crew_for.get(
                    row_key(race.at, race.runner_one, race.runner_two)), now)
            except Exception:
                self.logger.error(
                    'Error handling Autumn %s', race.match_id, exc_info=True)

    async def _handle(self, race, row, now):
        """One resolved race, at whatever stage it is at."""
        # The mirror first, because it is the cheapest thing to get wrong and the
        # only one anybody else reads. It is sent whenever the sheet disagrees
        # with what we last sent, which is what makes a reschedule propagate.
        await self._mirror(race)

        if race.conditional:
            # Scheduled but not raceable: the grand-final reset before the final
            # has been played, or a match whose racers are not both known. A time
            # is still mirrored -- the finalists agreed it -- but no room opens.
            return

        opens_at = race.at - timedelta(minutes=ROOM_OPEN_MINUTES_BEFORE)
        wakes_at = race.at - timedelta(minutes=BOOTH_WAKE_MINUTES_BEFORE)

        if now >= wakes_at and now < race.at + ROOM_OPEN_GRACE:
            await self._wake(race, row)

        if now < opens_at:
            return
        if now > race.at + ROOM_OPEN_GRACE:
            # The start has been and gone. An admin can still make a room; a
            # scheduler that does it hours late is just confusing.
            return

        await self._room(race, row)

    # -- the mirror --------------------------------------------------------

    async def _mirror(self, race):
        """Tell the engine when this match is, if we have not already.

        Keyed by the match, so a reschedule is a *changed* value rather than a
        second entry -- which is the whole reason the key is not the time.
        """
        key = self._match_key(race.match_id)
        when = race.at.isoformat()
        if self.mirrored.get(key) == when:
            return

        written = await self.engine.mirror_time(race.match_id, race.at)
        if written.ok:
            self.mirrored[key] = when
            self._save(self.mirrored_store, self.mirrored)
            return

        # Not recorded, or nobody knows. Either way the local note is *not*
        # updated, so the next tick tries again -- which is safe because the
        # write is idempotent, and necessary because an unconfirmed write may
        # never have landed.
        self.logger.warning(
            'Autumn: the engine has not confirmed %s at %s (%s: %s)',
            race.match_id, when, written.outcome, written.detail)

    # -- the side effects --------------------------------------------------

    async def _wake(self, race, row):
        """Ask the booth to wake, once per match per process.

        Not persisted. Waking a control plane twice is harmless -- it is already
        awake -- and a note that survives a restart would skip the wake after the
        restart that most needs it.
        """
        if self._wake_booth is None or race.match_id in self._woken:
            return
        channel = getattr(row, 'channel', '') if row else ''
        if not channel:
            # No channel on the row means no restream, which is most matches.
            return
        self._woken.add(race.match_id)
        try:
            await self._wake_booth(race, channel)
        except Exception:
            # A booth that will not wake costs a restream, not a race.
            self.logger.error(
                'Autumn: could not wake the booth for %s', race.match_id,
                exc_info=True)

    async def _room(self, race, row):
        """Open the race room, exactly once for this match."""
        if self._open_room is None:
            return
        key = self._match_key(race.match_id)
        if key in self.created:
            return

        # Claim before creating. A creation whose answer is lost is `uncertain`,
        # never `failed`: the room may exist, and trying again is how a match
        # ends up with two.
        url = await self._open_room(race, row)
        if not url:
            return
        self.created[key] = url
        self._save(self.created_store, self.created)

        if self._announce is not None:
            try:
                await self._announce(race, row, url)
            except Exception:
                self.logger.error(
                    'Autumn: could not announce %s', race.match_id, exc_info=True)

    # -- bookkeeping -------------------------------------------------------

    def _unscoped_bindings(self):
        """The bindings as the matcher wants them: row key -> match id.

        Stored with the competition in front so two tournaments cannot read each
        other's, and handed over without it because the matcher only ever deals
        with one.
        """
        prefix = '{}|'.format(self.event)
        return {
            key[len(prefix):]: value
            for key, value in self.bindings.items()
            if key.startswith(prefix)
        }

    def _remember(self, bindings):
        """Persist any binding the matcher made that we did not already have."""
        added = False
        for key, match_id in bindings.items():
            scoped = '{}|{}'.format(self.event, key)
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
        if added:
            self._save(self.bindings_store, self.bindings)

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

    def _save(self, store, entries):
        if store is None:
            return
        try:
            store.save(entries)
        except Exception:
            # Stop rather than carry on with state that is only in memory: the
            # next restart would forget it, and forgetting a room means making a
            # second one.
            self.stopped = True
            self.logger.error(
                'Autumn state could not be saved; the tournament runner is '
                'stopped', exc_info=True)
