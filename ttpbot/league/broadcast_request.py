"""Turn a scheduled League race into the control plane's booth request.

The endpoint contract lives in Z1RR.Restream:
`POST /internal/relay/league/broadcast`, documented in
`docs/internal/platform-configuration.md`. This module's only job is to
produce that body, or to decline.

Declining matters as much as producing. A race with no fixture has no week
number and no way to tell which team is away, and putting the teams on the
wrong sides of the canvas is worse than not building the booth at all - the
room, the racer invites and the Discord post all still go out either way.
"""

from datetime import timezone

#: Slot 1 is the away team, slot 2 the home team.
AWAY_SLOT = 1
HOME_SLOT = 2


def _title(race):
    """Two lines: the week, then the teams and the game.

    Team abbreviations, not full names: measured in a browser against the race
    scene's 432px header, "Three Unique Gamers vs. Midwest Is Best" is 494px
    and truncates, while the widest possible abbreviated pairing is 174px.
    """
    away = race.away_racer.team
    home = race.home_racer.team
    matchup = '{} vs. {}'.format(away, home)
    if race.game is not None:
        matchup += ' Game {}'.format(race.game)
    return 'Z1RR League Week {}\n{}'.format(race.fixture.week, matchup)


def _racer_slot(racer, slot):
    """One racer slot for the booth payload.

    The racetime id is the same key the racer is invited with, so sending it
    lets the control plane bind the slot to the race entrant exactly rather
    than inferring it from the Twitch channel. Omitted rather than sent empty
    when the roster has no id: the far end treats absence as "match by
    channel, as before", and an empty string would be a malformed id.
    """
    slot_payload = {
        'slot': slot,
        'channel': racer.twitch_channel,
        'displayName': racer.display_name,
    }
    racetime_id = (racer.racetime_id or '').strip()
    if racetime_id:
        slot_payload['racetimeId'] = racetime_id
    return slot_payload


def _crew_user_id(crew, name, logger):
    if not name:
        return None
    user_id = crew.user_id_for(name)
    if not user_id:
        # Named but unknown: the Comms/Tracker columns are a dropdown of these
        # names, so this means the roster moved on or somebody typed freehand.
        logger.warning('League crew %r did not resolve to a Z1RR.Restream user', name)
    return user_id


def build_broadcast_request(race, race_slug, crew, logger):
    """The booth request body, or None when this race must not get a booth.

    `crew` resolves a schedule name to a managed-user id - not a Discord id.
    A draft stores managed-user ids, and passing the other kind invites
    nobody, silently.
    """
    if not race.channel:
        # Nobody intends to restream this one.
        return None
    if race.fixture is None:
        logger.warning(
            'League race %s has no fixture; skipping the booth (room and '
            'announcement are unaffected)', race.slug,
        )
        return None
    if race.away_racer is None:
        # The fixture was found by these two teams, so one of them should be
        # the away side. Neither or both means the sheets drifted, and
        # guessing would put the teams on the wrong sides.
        logger.warning(
            'League race %s does not line up with fixture %s vs %s; skipping the booth',
            race.slug, race.fixture.away, race.fixture.home,
        )
        return None
    if not race_slug:
        # The draft carries raceSlug so the booth can sync flags, seed and
        # results. A booth with no room to sync from is not worth building.
        logger.warning('League race %s has no race room yet; skipping the booth', race.slug)
        return None

    commentators = [
        user_id for user_id in (
            _crew_user_id(crew, name, logger) for name in race.comms
        ) if user_id
    ]
    return {
        'leagueKey': race.key,
        'twitchChannel': race.channel,
        'raceSlug': race_slug,
        # UTC, matching the contract's own example. The sheet's times are US
        # Eastern, and an offset that travels with the value is one more thing
        # for the far end to get wrong.
        'scheduledAt': race.start.astimezone(timezone.utc).isoformat(),
        'title': _title(race),
        'racers': [
            _racer_slot(race.away_racer, AWAY_SLOT),
            _racer_slot(race.home_racer, HOME_SLOT),
        ],
        'commentatorUserIds': commentators,
        'trackerUserId': _crew_user_id(crew, race.tracker, logger),
    }
