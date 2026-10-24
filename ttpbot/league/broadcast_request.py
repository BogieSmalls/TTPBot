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


def _series_request(race, race_slug, crew, logger):
    """One booth for a series game: four racers, each row a pairing.

    Slots run row by row, lower seed then higher (1-2 the first pairing, 3-4
    the second), so a quad layout reads as two head-to-heads.
    """
    if not race.channel or not race_slug:
        return None
    lower, higher = race.away_runners, race.home_runners
    if len(lower) != 2 or len(higher) != 2 or None in lower + higher:
        logger.warning('League series game %s is not two pairings; skipping the booth', race.slug)
        return None
    matchup = '{} vs. {}'.format(lower[0].team, higher[0].team)
    if race.game is not None:
        matchup += ' Game {}'.format(race.game)
    trackers = getattr(race, 'trackers', ())
    return {
        'leagueKey': race.key,
        'twitchChannel': race.channel,
        'raceSlug': race_slug,
        'scheduledAt': race.start.astimezone(timezone.utc).isoformat(),
        'title': 'Z1RR League {}\n{}'.format(race.fixture.label, matchup),
        # Each racer carries their team, so the control plane gives the two
        # columns their team colors, as it does for a co-op booth.
        'racers': [dict(_racer_slot(racer, slot), team=racer.team) for slot, racer in
                   enumerate((lower[0], higher[0], lower[1], higher[1]), start=1)],
        'commentatorUserIds': [u for u in (_crew_user_id(crew, n, logger) for n in race.comms) if u],
        'trackerUserId': _crew_user_id(crew, trackers[0] if trackers else None, logger),
        # Team-scored by summed times: the finishing order is not the result.
        'series': True,
    }


def build_broadcast_request(race, race_slug, crew, logger, coop=False, series=False):
    if series:
        return _series_request(race, race_slug, crew, logger)
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
    payload = {
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
    if coop:
        # One runner from each team is on this booth; their finishing order
        # is not the match result, so the control plane keeps the winner
        # banner off for this race room.
        payload['coop'] = True
    return payload


#: The booth's crew limits: the control plane refuses a request naming more.
MAX_COMMENTATORS = 2


def build_coop_broadcast_request(match, race_slug, crew, logger):
    """One four-racer booth for a whole co-op match, or None.

    Both teams on one canvas: the away pair in slots 1-2 and the home pair in
    3-4, each carrying its team, and the control plane lays them out as team
    columns (blue left, pink right). It goes out on the first featured row's
    channel under that row's key, so it is the same booth a per-row request
    would have made there: switching modes mid-week replays that booth rather
    than staging a second one on the channel.

    None means "no four-racer booth", and the caller falls back to the per-row
    two-racer booths.
    """
    featured = match.featured_rows
    if not featured or not race_slug:
        return None
    primary = featured[0]
    runners = match.away_runners + match.home_runners
    channels = [(racer.twitch_channel or '').strip().casefold() for racer in runners]
    if not all(channels) or len(set(channels)) != 4:
        # The canvas needs four distinct streams; the control plane would
        # refuse anything else, so say why here instead.
        logger.warning(
            'League co-op %s: the four runners do not have four distinct Twitch '
            'channels; using per-row booths', match.slug,
        )
        return None
    names = list(match.comms)
    if len(names) > MAX_COMMENTATORS:
        logger.warning('League co-op %s names %d commentators; the booth takes the first %d (%s)',
                       match.slug, len(names), MAX_COMMENTATORS, ', '.join(names[:MAX_COMMENTATORS]))
    trackers = list(match.trackers)
    if len(trackers) > 1:
        logger.warning('League co-op %s names %d trackers; the booth takes %s',
                       match.slug, len(trackers), trackers[0])
    commentators = [
        user_id for user_id in (
            _crew_user_id(crew, name, logger) for name in names[:MAX_COMMENTATORS]
        ) if user_id
    ]
    away_team = primary.away_racer.team
    home_team = primary.home_racer.team
    racers = []
    for index, racer in enumerate(match.away_runners):
        racers.append(dict(_racer_slot(racer, index + 1), team=away_team))
    for index, racer in enumerate(match.home_runners):
        racers.append(dict(_racer_slot(racer, index + 3), team=home_team))
    return {
        'leagueKey': primary.key,
        'twitchChannel': primary.channel,
        'raceSlug': race_slug,
        'scheduledAt': match.start.astimezone(timezone.utc).isoformat(),
        'title': _title(primary),
        'racers': racers,
        'commentatorUserIds': commentators,
        'trackerUserId': _crew_user_id(crew, trackers[0] if trackers else None, logger),
        'coop': True,
    }
