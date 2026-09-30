"""Assembling the Autumn runner from the environment.

One place that knows which real thing each of the scheduler's collaborators is,
so the scheduler itself stays testable with none of them. Everything is optional
and absence is reported rather than raised: a relay missing the engine token, the
sheet URL or the racetime credentials should run the League and say nothing about
Autumn.

What is wired for real here:

    the Schedule tab          read over HTTPS, cached, sign-in page detected
    the engine                the draw, the aliases, the time mirror
    the race room             created at racetime, recovered when uncertain
    the T-35 control plane    the relay's own /api/wake, which waits for ready
    the announcement          a Discord webhook

Two things are deliberately *not* wired, both for the same reason: the only way
to do them properly changes a system that is live for something else.

**The invites.** The League invites over the racetime websocket, through
`handler.invite_user`, driven by `bot.state[race_name]['league_race']['invite']`
-- and the handler's whole invite path is League-shaped, down to the
`league_invited` guard. Teaching it about a tournament means editing a code path
TTP Season 5 and the League both run on. So the scheduler's `invite` seam is left
empty rather than filled with something invented: an earlier draft of this POSTed
to `/o/<category>/<room>/invite`, which does not appear anywhere in this codebase
and was a guess. The room is created with `invitational: false`, so racers can
join it themselves in the meantime.

**The booth handoff.** `request_booth` posts to
`/internal/relay/league/broadcast`, which belongs to the broadcast system, and
there is no tournament equivalent. Inventing one means changing Z1RR.Restream,
which is live for the League. So the control plane *is* woken at T-35 -- that is
the relay's own endpoint and competition-agnostic -- and how a tournament match
reaches a booth is left to whoever owns the broadcast side.
"""

from ..config import TIMEZONE
from .announce import send_autumn_announcement
from .engine import engine_from_env
from .rooms import create_autumn_room
from .scheduler import AutumnScheduler
from .source import AutumnSource

#: The Schedule tab of the League master sheet, exported as CSV. The tab is read
#: rather than the form's responses, because the tab is what a council member
#: edits when a race moves.
DEFAULT_SCHEDULE_URL = (
    'https://docs.google.com/spreadsheets/d/'
    '1cKqpvwSAsaoYhNJUZSeBCqJ_D_it2W2nb-V0cr_1V5Q/export?format=csv&gid=2033319762'
)


def _round_label(match_id, size=None):
    """A human name for a match, or None.

    Only the shapes worth naming. Everything else falls back to the match id,
    which is never wrong even when it is not friendly -- a label guessed from a
    bracket this does not model would put "Semifinal" on the wrong room.
    """
    if match_id == 'GF-1':
        return 'Grand Final'
    if match_id == 'GF-2':
        return 'Grand Final Reset'
    return None


class AutumnRunner:
    """The scheduler plus the adapters it needs, ready to tick."""

    def __init__(self, scheduler, engine, logger):
        self.scheduler = scheduler
        self.engine = engine
        self.logger = logger

    @property
    def configured(self):
        return self.scheduler is not None and self.scheduler.configured

    async def run(self):
        if not self.configured:
            self.logger.info('Autumn: not configured, so the runner stays off')
            return
        await self.scheduler.run()


def build_autumn_runner(env, bot, logger, stores=None, event='autumn'):
    """The runner, or one whose `configured` is False.

    `bot` supplies the racetime provider, the access token and the Discord
    webhook -- the same objects the League's scheduler is handed, so a tournament
    room is opened against exactly the destination a League room is.

    `stores` is a mapping of the four entry kinds to `DestinationStateStore`s.
    Built by the caller because the caller owns the data directory and the
    destination key, and both are checked by the store itself.
    """
    engine = engine_from_env(env, event=event, logger=logger)
    url = (env.get('Z1RR_AUTUMN_SCHEDULE_URL') or '').strip() or DEFAULT_SCHEDULE_URL
    source = AutumnSource(url, logger)
    stores = stores or {}

    if engine is None:
        # Said once, by `engine_from_env`. A runner is still returned so a caller
        # does not have to special-case None.
        return AutumnRunner(None, None, logger)

    async def open_room(race, row):
        return await create_autumn_room(
            race, bot.provider, getattr(bot, 'access_token', None), logger,
            label=_round_label(race.match_id))

    async def announce(race, row, url):
        # The ids come from the engine, which resolves a name through `seatFor` --
        # exact, then flattened, then the aliases -- so a racer is pinged whichever
        # way the form spelled them.
        ids = await _racer_ids(engine, race, logger)
        posted = await send_autumn_announcement(
            race, url, getattr(bot, 'autumn_webhook_url', None), logger,
            ids=ids, label=_round_label(race.match_id),
            crew=tuple(getattr(row, 'crew', ()) or ()))
        if not posted:
            # Raised rather than returned, because the scheduler's guard is set
            # from "did this not raise" and a False here must be a retry.
            raise RuntimeError('the Discord webhook did not accept it')

    wake = _wake_adapter(env, logger)

    scheduler = AutumnScheduler(
        source=source,
        engine=engine,
        logger=logger,
        bindings_store=stores.get('autumn_bindings'),
        created_store=stores.get('autumn_created_races'),
        mirrored_store=stores.get('autumn_mirrored_times'),
        announced_store=stores.get('autumn_sent_webhooks'),
        open_room=open_room,
        wake_booth=wake,
        announce=announce,
        # invite: see the module docstring. The seam is here and nothing is in it.
        invite=None,
        event=event,
    )
    return AutumnRunner(scheduler, engine, logger)


async def _racer_ids(engine, race, logger):
    """Both racers' Discord ids, by canonical name, as far as the engine knows."""
    try:
        state = await engine.state()
    except Exception as exc:
        # Announced with plain names rather than not announced. A race nobody was
        # told about is worse than a race announced without pings.
        logger.warning(
            'Autumn: could not read racer ids for %s (%s); announcing with names',
            race.match_id, exc)
        return {}
    racers = ((state.get('document') or {}).get('racers') or {})
    return {
        name: racers[name]
        for name in (race.runner_one, race.runner_two)
        if racers.get(name)
    }


def _wake_adapter(env, logger):
    """The T-35 control-plane wake, or None when the relay is not configured.

    The relay's `/api/wake` reads the instance state, starts it only if needed,
    and waits for *readiness* rather than for the instance to report RUNNING --
    which is what makes a T-35 wake safe for a T-30 room rather than merely
    hopeful. It is competition-agnostic, so the League's helper is used as-is.
    """
    relay = (env.get('Z1RR_RELAY_URL') or '').strip()
    token = (env.get('Z1RR_WAKE_TOKEN') or '').strip()
    if not (relay and token):
        logger.info(
            'Autumn: no relay wake configured, so no booth is woken for '
            'restreamed matches')
        return None

    from ..league.wake import wake_control_plane

    async def wake(race, channel):
        logger.info(
            'Autumn: waking the control plane for %s on %s',
            race.match_id, channel)
        await wake_control_plane(
            relay, logger, token=token,
            target=(env.get('Z1RR_WAKE_TARGET') or 'production').strip())

    return wake
