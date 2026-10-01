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
    the invites               seeded for the room's own handler to send
    the T-35 control plane    the relay's own /api/wake, which waits for ready
    the announcement          a Discord webhook

Invites go the way the League's do, which is the only way racetime offers: the
handler that holds the room's websocket sends them, and the scheduler's job is to
write the list where that handler reads it. An earlier draft of this POSTed to
`/o/<category>/<room>/invite`, which was a guess -- no such call appears anywhere
in this codebase -- and it was removed rather than shipped.

The racetime ids come from the bracket engine, beside the Discord ids, because
nothing else held them: the League's committed roster covers 34 of the Autumn
field's 61 and not `chessjerk`, who is in the first match. A racer with no id on
file is named in the log and can join the room themselves, since it is created
with `invitational: false`.

The booth handoff uses the same crew directory and request transport as League,
with tournament identity and the tournament route on the control plane.
"""

from urllib.parse import urlsplit

from ..config import TIMEZONE
from .announce import send_autumn_announcement
from .engine import engine_from_env
from .rooms import create_autumn_room, room_title, recover_autumn_room
from .scheduler import AutumnScheduler
from .source import AutumnSource
from .booths import AutumnBooths
from ..league.crew import CrewDirectory
from ..paths import runtime_path

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

    def __init__(self, scheduler, engine, logger, results=None):
        self.scheduler = scheduler
        self.engine = engine
        self.logger = logger
        self.results = results

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

    `stores` is a mapping of the entry kinds to `DestinationStateStore`s.
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

    # Who is who on racetime, read from the engine and kept until a name is
    # wanted that is not in it.
    #
    # Not per tick, because it changes when an operator adds an id rather than
    # when a race happens; and not once at startup either, because an id added
    # mid-season should reach a room opened an hour later without a restart. So:
    # cached, and re-read exactly when it would otherwise have to say "no id".
    results = None
    if stores.get('autumn_results') is not None:
        from .results import AutumnResults
        results = AutumnResults(store=stores['autumn_results'], engine=engine,
                                provider=bot.provider, logger=logger, event=event,
                                edition=(env.get('Z1RR_AUTUMN_EDITION') or '2026').strip())
    racetime_ids = {}
    racetime_ids.update(getattr(bot, 'autumn_racetime_ids', None) or {})

    async def open_room(race, row):
        return await create_autumn_room(
            race, bot.provider, getattr(bot, 'access_token', None), logger,
            label=_round_label(race.match_id))

    async def recover_room(race, action):
        return await recover_autumn_room(race, bot.provider, getattr(bot, 'access_token', None), logger,
            label=_round_label(race.match_id))

    async def seed_invites(race, url):
        """Write the invite list where the room's handler will read it.

        `bot.state[race_name]` is created by `create_handler` only when absent and
        passed to the handler by reference, so seeding it here reaches the handler
        untouched -- the same mechanism the League uses.
        """
        name = _race_name(url)
        if not name:
            return

        wanted = (race.runner_one, race.runner_two)
        if not all(racetime_ids.get(who) for who in wanted):
            # One of them is unknown. Ask the engine once before giving up on
            # them, so an id filled in this afternoon reaches a room opened
            # tonight without a restart.
            racetime_ids.update(await _racetime_ids(engine, logger))

        ids = [racetime_ids.get(who) for who in wanted]
        if results is not None:
            try:
                key = results.bind(race, url, racetime_ids)
                await results.publish_binding(key, results.store.load()[key])
            except Exception:
                logger.error('Autumn result receipt could not be saved for %s; invitations still proceed', race.match_id, exc_info=True)
        entry = bot.state.setdefault(name, {})
        entry['autumn_race'] = {
            # Both or neither. A one-element list would have the handler invite
            # one racer and leave the other looking at a room they are not in,
            # which is worse than inviting nobody and saying so.
            'invite': [who for who in ids if who] if all(ids) else [],
            'title': room_title(race, _round_label(race.match_id)),
        }
        if not all(ids):
            missing = [
                name for name, who in zip(
                    (race.runner_one, race.runner_two), ids) if not who
            ]
            logger.warning(
                'Autumn: no racetime id for %s, so nobody is invited to %s; the '
                'room is open and they can join it themselves',
                ', '.join(missing), race.match_id)

        # begin() may already have run after a restart or before this tick.
        # The handler owns the websocket and its invite guard in either order.
        send_invites = entry.get('_autumn_send_invites')
        if send_invites is not None:
            await send_invites()

    async def announce(race, row, url, booth=None, correction=False):
        # The ids come from the engine, which resolves a name through `seatFor` --
        # exact, then flattened, then the aliases -- so a racer is pinged whichever
        # way the form spelled them.
        ids = await _racer_ids(engine, race, logger)
        posted = await send_autumn_announcement(
            race, url, (env.get('TTPBOT_AUTUMN_DISCORD_WEBHOOK_URL') or '').strip()
            or getattr(bot, 'autumn_webhook_url', None), logger,
            ids=ids, label=_round_label(race.match_id),
            continuation=bool(booth and booth.is_continuation), correction=correction,
            crew=tuple(getattr(row, 'crew', ()) or ()),
            channel_id=(env.get('TTPBOT_AUTUMN_DISCORD_CHANNEL_ID') or '').strip(),
            bot_token=(env.get('TTPBOT_AUTUMN_DISCORD_BOT_TOKEN')
                       or env.get('TTPBOT_LEAGUE_DISCORD_BOT_TOKEN') or '').strip())
        if not posted:
            # Raised rather than returned, because the scheduler's guard is set
            # from "did this not raise" and a False here must be a retry.
            raise RuntimeError('Discord did not accept the announcement')

    async def announce_continuation(race, row, url):
        await announce(race, row, url, correction=True)

    wake = _wake_adapter(env, logger)
    booth_url = (env.get('Z1RR_CONTROL_PLANE_URL') or '').strip()
    booth_token = (env.get('Z1RR_ROSTER_TOKEN') or '').strip()
    booths = None
    if booth_url and booth_token:
        booths = AutumnBooths(
            engine=engine, crew=CrewDirectory(runtime_path('autumn_crew.json', env=env), logger),
            logger=logger, base_url=booth_url, token=booth_token,
            edition=(env.get('Z1RR_AUTUMN_EDITION') or '2026').strip(), wake=wake,
            roster_url=(env.get('Z1RR_ROSTER_URL') or '').strip() or None)

    scheduler = AutumnScheduler(
        source=source,
        engine=engine,
        logger=logger,
        bindings_store=stores.get('autumn_bindings'),
        created_store=stores.get('autumn_created_races'),
        mirrored_store=stores.get('autumn_mirrored_times'),
        announced_store=stores.get('autumn_sent_webhooks'),
        booth_notice_store=stores.get('autumn_booth_notices'),
        announce_continuation=announce_continuation,
        open_room=open_room,
        recover_room=recover_room,
        wake_booth=booths.prepare if booths else wake,
        request_booth=booths.request if booths else None,
        announce=announce,
        invite=seed_invites,
        event=event,
        recover_results=results.recover if results else None,
    )
    return AutumnRunner(scheduler, engine, logger, results)


async def _racetime_ids(engine, logger):
    """Every racetime id the engine holds, by the draw's spelling of the name."""
    try:
        state = await engine.state()
    except Exception as exc:
        logger.warning(
            'Autumn: could not read racetime ids (%s); nobody new is invited', exc)
        return {}
    return dict((state.get('document') or {}).get('racetimeIds') or {})


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


def _race_name(room_url):
    """The category and slug, matching racetime data and Bot.create_handler."""
    return urlsplit(room_url or '').path.strip('/')
