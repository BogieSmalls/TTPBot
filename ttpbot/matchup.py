"""Head-to-head records for !matchup, read from Randometrics.

The wording follows Z1RR.Restream's !matchup (mini/server/chat/commands/
matchup.ts), minus the z1rracing.com link it appends for Twitch.

The API at api.z1rracing.com resolves racers by account id only, so a name
typed in chat is first looked up with /v1/racers?q=. That search matches
substrings -- "bo" finds Bogie, Bort and four others -- so an exact name wins,
and a partial match is accepted only when it is the sole result. Anything else
is reported as ambiguous rather than answered with a stranger's record.
"""

import asyncio
from urllib.parse import quote

import aiohttp

STATS_API = 'https://api.z1rracing.com'
TIMEOUT_SECONDS = 5


class MatchupError(Exception):
    """A reply that explains why there is no record to give."""


async def fetch_json(path):
    """GET a Randometrics path. None for any failure, never an exception."""
    try:
        async with aiohttp.request(
            'GET', f'{STATS_API}{path}',
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        ) as response:
            if response.status != 200:
                return None
            return await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


def clean_name(typed):
    """Drop what chat adds to a name: an @ and a racetime #1234 discriminator."""
    return typed.strip().lstrip('@').split('#', 1)[0].strip()


async def resolve_racer(typed, fetch=None):
    """(account id, canonical name) for a typed name, or raise MatchupError."""
    fetch = fetch or fetch_json
    name = clean_name(typed)
    if not name:
        raise MatchupError('Usage: !matchup <racer1> <racer2>')
    found = await fetch(f'/v1/racers?q={quote(name)}')
    if found is None:
        raise MatchupError("Couldn't reach the Z1RR stats right now. Try again in a bit.")
    racers = [r for r in found.get('racers') or [] if r.get('id') and r.get('name')]

    exact = [r for r in racers if r['name'].lower() == name.lower()]
    if len(exact) == 1:
        match = exact[0]
    elif len(racers) == 1 and not exact:
        match = racers[0]
    elif not racers:
        raise MatchupError(f'No racer named {name} in the Z1RR stats.')
    else:
        options = ', '.join(r['name'] for r in racers[:6])
        more = ', ...' if len(racers) > 6 else ''
        raise MatchupError(f'"{name}" matches more than one racer: {options}{more}')
    return match['id'], match['name']


def _record(a, b, ahead):
    """'Bogie ahead 70-44', or 'level 5-5' when neither is."""
    if ahead['a'] == ahead['b']:
        return f"level {ahead['a']}-{ahead['b']}"
    leader, high, low = (a, ahead['a'], ahead['b']) if ahead['a'] > ahead['b'] else (b, ahead['b'], ahead['a'])
    return f'{leader} ahead {high}-{low}'


def _head_to_head(a, b, one):
    if one['total'] == 0:
        return 'they have never raced head-to-head'
    if one['total'] == 1:
        return f"1 head-to-head, won by {a if one['a'] == 1 else b}"
    if one['a'] == one['b']:
        return f"{one['total']} head-to-heads, split {one['a']}-{one['b']}"
    leader, high, low = (a, one['a'], one['b']) if one['a'] > one['b'] else (b, one['b'], one['a'])
    return f"{one['total']} head-to-heads, {leader} leading {high}-{low}"


def format_matchup(a, b, summary):
    """One sentence describing a Randometrics matchup summary.

    All three figures, because any one alone misleads: two racers can be
    6-4 across a season and never have actually met.
    """
    names = f'{a} vs {b}:'
    if summary['common'] == 0:
        return f'{names} no shared races yet.'
    return (
        f"{names} {summary['common']} mutual races, {_record(a, b, summary['ahead'])}, "
        f"and {_head_to_head(a, b, summary['oneOnOnes'])}."
    )


def _valid_summary(summary):
    """Present but malformed is absent: half a summary is a confident wrong number."""
    if not isinstance(summary, dict) or not isinstance(summary.get('common'), int):
        return False
    ahead, one = summary.get('ahead'), summary.get('oneOnOnes')
    return (
        isinstance(ahead, dict) and isinstance(ahead.get('a'), int) and isinstance(ahead.get('b'), int)
        and isinstance(one, dict) and isinstance(one.get('total'), int)
    )


async def matchup_reply(typed_a, typed_b, fetch=None):
    """The chat reply for !matchup: the record, or why there isn't one."""
    fetch = fetch or fetch_json
    try:
        (id_a, name_a), (id_b, name_b) = await asyncio.gather(
            resolve_racer(typed_a, fetch), resolve_racer(typed_b, fetch),
        )
    except MatchupError as exc:
        return str(exc)
    if id_a == id_b:
        return f'{name_a} vs {name_a}: pick two different racers.'

    body = await fetch(f'/v1/matchups/{quote(id_a)}/{quote(id_b)}')
    summary = (body or {}).get('summary')
    if not _valid_summary(summary):
        return "Couldn't read the Z1RR stats for that matchup right now."
    summary = dict(summary, oneOnOnes={
        'total': summary['oneOnOnes']['total'],
        'a': summary['oneOnOnes'].get('a', 0),
        'b': summary['oneOnOnes'].get('b', 0),
    })
    return format_matchup(name_a, name_b, summary)
