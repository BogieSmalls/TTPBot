"""Drive the real League scheduler at a staging control plane for one test co-op match.

The production scheduler, its state and its credentials are not involved:
everything runs in this process with a throwaway state directory.

  - The match is two Schedule rows (one co-op fixture), so grouping,
    LEAGUE_COOP_FOUR_RACER_BOOTH, the booth payload, retries and duplicate
    prevention run as they do on a race night.
  - No racetime room is created and no Discord post is sent: the room is an
    existing one you name (seeded as already created), and the webhook and
    threads are off.
  - The booth request goes to the staging CP named here, with the staging
    booth token from Z1RR_BOOTH_TOKEN (never printed). It stages a real booth
    on that channel, which boots a staging encoder: run this only inside an
    agreed validation window, and stop the booth afterwards.

    python scripts/staging_coop_rehearsal.py --cp https://staging.restream.z1rracing.com \\
        --room https://racetime.gg/z1r/some-room-1234 --channel TriforceTriplePlay \\
        --away alpha,bravo --home charlie,delta [--ticks 2]

Racer names are Twitch channels (and display names); each gets a test
racetime id. Prints each booth request's payload (no token) and its outcome.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ttpbot.config import TIMEZONE  # noqa: E402
from ttpbot.league import scheduler as scheduler_module  # noqa: E402
from ttpbot.league.booth import BoothOutcome, request_booth  # noqa: E402
from ttpbot.league.coop import CoopMatch  # noqa: E402
from ttpbot.league.matchups import Fixture  # noqa: E402
from ttpbot.league.roster import Racer  # noqa: E402
from ttpbot.league.schedule import LeagueRace  # noqa: E402
from ttpbot.league.scheduler import LeagueScheduler  # noqa: E402
from ttpbot.state import DestinationStateStore  # noqa: E402

DESTINATION = 'https://racetime.gg|z1r'


class _Source:
    def __init__(self, races):
        self._races = races

    async def races(self, now):
        return list(self._races)


class _Bot:
    def __init__(self):
        self.state = {}
        self.access_token = None
        self.provider = None


class _Crew:
    """Crew names map to staging user ids given on the command line; no roster lookups."""

    def __init__(self, mapping):
        self._mapping = mapping

    async def refresh(self, url, token):
        return True

    def user_id_for(self, name):
        return self._mapping.get(name)

    def mentions(self, names):
        return [name for name in names if name], []


def _racer(name, team, team_full):
    return Racer(sheet_name=name, team=team, team_full=team_full, display_name=name,
                 twitch_channel=name.lower(), racetime_id='rehearsal-' + name.lower(), discord_id='')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--cp', required=True, help='staging control plane base URL')
    parser.add_argument('--room', required=True, help='an existing racetime room URL (nothing is created)')
    parser.add_argument('--channel', default='TriforceTriplePlay')
    parser.add_argument('--second-channel', default=None, help='a second featured row channel, to check one booth')
    parser.add_argument('--away', required=True, help='two away runners (Twitch channels), comma separated')
    parser.add_argument('--home', required=True, help='two home runners (Twitch channels), comma separated')
    parser.add_argument('--comms', default='', help='crew name=staging user id pairs, comma separated')
    parser.add_argument('--tracker', default='', help='crew name=staging user id')
    parser.add_argument('--ticks', type=int, default=2)
    parser.add_argument('--dry-run', action='store_true', help='print the requests, send nothing (answers staged)')
    args = parser.parse_args()

    if 'staging' not in args.cp:
        sys.exit('refusing: --cp must be a staging control plane')
    token = os.environ.get('Z1RR_BOOTH_TOKEN', '').strip() or ('dry-run' if args.dry_run else '')
    if not token:
        sys.exit('set Z1RR_BOOTH_TOKEN to the staging booth token')
    away = [name.strip() for name in args.away.split(',') if name.strip()]
    home = [name.strip() for name in args.home.split(',') if name.strip()]
    if len(away) != 2 or len(home) != 2:
        sys.exit('--away and --home take two runners each')

    pairs = [pair.split('=', 1) for pair in args.comms.split(',') if '=' in pair]
    tracker = args.tracker.split('=', 1) if '=' in args.tracker else None
    crew = _Crew(dict(pairs + ([tracker] if tracker else [])))

    start = datetime.now(TIMEZONE).replace(second=0, microsecond=0) + timedelta(minutes=30)
    fixture = Fixture(week=99, away='Rehearsal Away', home='Rehearsal Home', label='Week 99 - Coop Rehearsal')
    rows = [
        LeagueRace(start=start, runner_one=_racer(away[0], 'RA', 'Rehearsal Away'), runner_two=_racer(home[0], 'RH', 'Rehearsal Home'),
                   channel=args.channel, comms=tuple(name for name, _ in pairs), tracker=tracker[0] if tracker else None,
                   game=1, fixture=fixture),
        LeagueRace(start=start, runner_one=_racer(home[1], 'RH', 'Rehearsal Home'), runner_two=_racer(away[1], 'RA', 'Rehearsal Away'),
                   channel=args.second_channel, game=1, fixture=fixture),
    ]

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    logger = logging.getLogger('coop-rehearsal')

    sent = []

    async def recording_request_booth(payload, base_url, booth_token, log, **kwargs):
        print('booth request:', json.dumps(payload, indent=2, sort_keys=True))
        outcome = BoothOutcome('staged', 'dry-run') if args.dry_run else await request_booth(payload, base_url, booth_token, log, **kwargs)
        print('booth outcome:', outcome)
        sent.append((payload, outcome))
        return outcome

    scheduler_module.request_booth = recording_request_booth

    async def run():
        with TemporaryDirectory() as directory:
            root = Path(directory)
            created = DestinationStateStore('league_races.json', DESTINATION, 'league_created_races', data_dir=root)
            webhooks = DestinationStateStore('league_webhooks.json', DESTINATION, 'league_sent_webhooks', data_dir=root)
            created.save({CoopMatch(rows=tuple(rows)).key: args.room})
            scheduler = LeagueScheduler(
                bot=_Bot(), source=_Source(rows), created_store=created, webhook_store=webhooks,
                webhook_url=None, logger=logger, crew=crew, booth_url=args.cp, booth_token=token,
                coop_four_racer_booth=True,
            )
            for tick in range(args.ticks):
                now = start - timedelta(minutes=30 - tick)
                print('--- tick {} at T-{} min'.format(tick + 1, 30 - tick))
                await scheduler.tick(now)
        print('--- {} booth request(s) sent'.format(len(sent)))

    asyncio.run(run())


if __name__ == '__main__':
    main()
