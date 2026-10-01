"""Map an engine match and authoritative sheet row onto the shared booth contract."""
from datetime import timezone
import re
from urllib.parse import urlsplit


def build_broadcast_request(race, row, room_url, document, crew, logger, *, edition, number=None):
    if row is None or not row.channel:
        return None
    slug = urlsplit(room_url or '').path.strip('/')
    if not slug or len(slug.split('/')) != 2:
        logger.warning('Autumn %s has no usable race room for a booth', race.match_id)
        return None
    streams = document.get('twitchChannels') or {}
    ids = document.get('racetimeIds') or {}
    ranks = document.get('ranks') or {}
    racers = []
    for slot, name in enumerate((race.runner_one, race.runner_two), start=1):
        stream = streams.get(name)
        if not isinstance(stream, str) or not re.fullmatch(r'[a-zA-Z0-9_]{1,25}', stream) or not ids.get(name):
            logger.warning('Autumn %s: %s needs a verified Twitch channel and racetime id; room is unaffected', race.match_id, name)
            return None
        racer = {'slot': slot, 'channel': stream.lower(), 'displayName': name, 'racetimeId': ids[name]}
        rank = ranks.get(name)
        if isinstance(rank, int) and not isinstance(rank, bool) and 1 <= rank <= 99:
            racer['tournamentSeed'] = '#{}'.format(rank)
        racers.append(racer)
    if len({r['channel'] for r in racers}) != 2 or len({r['racetimeId'] for r in racers}) != 2:
        logger.warning('Autumn %s: the two racers have conflicting stream/account identities', race.match_id)
        return None

    def crew_id(name):
        if not name:
            return None
        who = crew.user_id_for(name) if crew else None
        if not who:
            logger.warning('Autumn crew %r has no managed Restream user id', name)
        return who

    event = race.identity.event
    game = race.identity.game
    label = '#{}'.format(number) if number is not None else race.match_id
    return {
        'requestKey': 'tournament:{}:{}:{}:game:{}'.format(event, edition, race.match_id, game),
        'competition': event, 'edition': edition, 'matchId': race.match_id, 'game': game,
        'twitchChannel': row.channel, 'raceSlug': slug,
        'scheduledAt': race.at.astimezone(timezone.utc).isoformat(),
        'title': 'Z1R Autumn Tournament {}\n{} vs {}'.format(label, race.runner_one, race.runner_two),
        'racers': racers,
        'commentatorUserIds': [who for who in (crew_id(row.comms_one), crew_id(row.comms_two)) if who],
        'trackerUserId': crew_id(row.tracker),
    }
