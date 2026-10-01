"""Suggest an Autumn result; only the operator's engine command accepts it.

Room receipts bind edition, match and game before the race. Neither a name pair
nor the currently ready bracket match can identify a finished final/reset.
"""
import asyncio
from decimal import Decimal, InvalidOperation
import json
import shlex

import aiohttp

from ..league.results import DURATION
from ..state import StateStoreError


def seconds(value):
    found = DURATION.fullmatch(str(value or ''))
    if not found or not any(found.groupdict().values()):
        return None
    try:
        parts = {key: Decimal(raw or '0') for key, raw in found.groupdict().items()}
        total = parts['days'] * 86400 + parts['hours'] * 3600 + parts['minutes'] * 60 + parts['seconds']
        return total if total.is_finite() and total > 0 else None
    except InvalidOperation:
        return None


def command_for(key, entry, *, event='autumn'):
    if entry['status'] != 'suggested':
        return None
    _, match, game = key.split('|')
    # Preview first. The operator verifies the room before adding --commit.
    return shlex.join(['node', 'scripts/tournament.mjs', 'result', match,
                       entry['winner'], '--event', event, '--game', game])


class AutumnResults:
    def __init__(self, *, store, engine, provider, logger, event='autumn', edition='2026', reader=None):
        self.store, self.engine, self.provider, self.logger = store, engine, provider, logger
        self.event, self.scope = event, '{}-{}'.format(event, edition)
        self.reader = reader or self._read_room
        self._lock = asyncio.Lock()

    def bind(self, race, room, racers):
        room = self.provider.resolve_location(room)
        key = '{}|{}|{}'.format(self.scope, race.match_id, race.identity.game)
        wanted = {name: racers.get(name) for name in (race.runner_one, race.runner_two)}
        entries = self.store.load()
        previous = entries.get(key)
        if previous:
            if previous['room'] != room or previous['racers'] != wanted:
                raise StateStoreError('Autumn result receipt conflicts with its original room/racers')
            return
        if any(value['room'] == room for value in entries.values()):
            raise StateStoreError('Autumn room already belongs to a different game')
        entries[key] = dict(room=room, racers=wanted, status='tracking', winner=None, reason=None)
        self.store.save(entries)

    async def _read_room(self, room):
        # resolve_location has already bound this to the configured provider.
        async with aiohttp.request('GET', room.rstrip('/') + '/data',
                                   timeout=aiohttp.ClientTimeout(total=20),
                                   allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError('room read returned HTTP {}'.format(response.status))
            return await response.json(content_type=None)

    async def record(self, data):
        if not isinstance(data, dict):
            return
        status = (data.get('status') or {}).get('value')
        if status not in ('finished', 'cancelled'):
            return
        name = data.get('name')
        if not isinstance(name, str):
            return
        room = self.provider.resolve_location('/' + name.lstrip('/'))
        async with self._lock:
            entries = self.store.load()
            found = [(key, value) for key, value in entries.items()
                     if key.startswith(self.scope + '|') and value['room'] == room]
            if len(found) != 1:
                return
            key, saved = found[0]
            if saved['status'] != 'tracking':
                return
            document = (await self.engine.state()).get('document') or {}
            winner, reason = self._winner(data, saved, document)
            _, match, game = key.split('|')
            draw = await self.engine.draw()
            current = next((m for m in draw.get('matches', []) if m.get('id') == match), None)
            if not current or {current.get('a'), current.get('b')} != set(saved['racers']):
                winner, reason = None, 'The bracket pairing differs from the saved room receipt'
            outcome = 'suggested' if winner else 'review'
            recorded = next((g for g in (document.get('games') or {}).get(match, [])
                             if g.get('game') == int(game)), None)
            if recorded:
                if winner and recorded.get('winner') == winner:
                    outcome = 'recorded'
                else:
                    outcome, winner, reason = 'review', None, 'Engine already records a different result; inspect it before correcting anything'
            elif (document.get('results') or {}).get(match):
                outcome, winner, reason = 'review', None, 'Match already decided without this game; inspect the engine result'
            updated = dict(saved, status=outcome, winner=winner, reason=reason)
            # Re-read after the awaited engine read; the scheduler may have bound
            # another room while this coroutine yielded.
            entries = self.store.load()
            if entries.get(key) != saved:
                return
            entries[key] = updated
            self.store.save(entries)
            self.logger.info('Autumn result %s %s: %s %s', key, outcome, room,
                             command_for(key, updated, event=self.event) or reason or '')

    @staticmethod
    def _winner(data, saved, document):
        if data['status']['value'] == 'cancelled':
            return None, 'Cancelled room; no result proposed'
        ids = document.get('racetimeIds') or {}
        if any(ids.get(name) != identifier for name, identifier in saved['racers'].items()):
            return None, 'Expected racetime IDs no longer agree with the engine'
        entrants = data.get('entrants') or []
        if (len(entrants) != 2 or
                {e.get('user', {}).get('id') for e in entrants} != set(saved['racers'].values())):
            return None, 'Room does not contain exactly the two expected racetime IDs'
        if any(e.get('status', {}).get('value') != 'done' for e in entrants):
            return None, 'DNF, DQ, forfeit or inconsistent finish needs an operator'
        times = [seconds(e.get('finish_time')) for e in entrants]
        if any(t is None for t in times) or times[0] == times[1]:
            return None, 'Missing finish time or tie needs an operator'
        identifier = entrants[0 if times[0] < times[1] else 1]['user']['id']
        return next(name for name, value in saved['racers'].items() if value == identifier), None

    async def recover(self):
        # A restart after racetime removes a finished room from current_races
        # still finds it by the room URL saved before the race.
        entries = self.store.load()
        if any(value['status'] == 'suggested' for value in entries.values()):
            document = (await self.engine.state()).get('document') or {}
            # Synchronous load/update/save after the await preserves new bindings.
            entries = self.store.load()
            changed = False
            for key, entry in entries.items():
                if not key.startswith(self.scope + '|') or entry['status'] != 'suggested':
                    continue
                _, match, game = key.split('|')
                recorded = next((g for g in (document.get('games') or {}).get(match, [])
                                 if g.get('game') == int(game)), None)
                if recorded:
                    agrees = recorded.get('winner') == entry['winner']
                    entries[key] = dict(entry, status='recorded' if agrees else 'review',
                                        winner=entry['winner'] if agrees else None,
                                        reason=None if agrees else 'Engine result differs from the suggestion')
                    changed = True
            if changed:
                self.store.save(entries)
        async def check(entry):
            try:
                data = await self.reader(entry['room'])
                received = self.provider.resolve_location('/' + str(data.get('name', '')).lstrip('/'))
                if received != entry['room']:
                    raise RuntimeError('Room response identity does not match its saved URL')
                await self.record(data)
            except Exception:
                self.logger.warning('Autumn result read failed for %s; kept for recovery', entry['room'], exc_info=True)
        await asyncio.gather(*(check(value) for key, value in entries.items()
                               if key.startswith(self.scope + '|') and value['status'] == 'tracking'))


def main():
    import argparse
    from pathlib import Path
    from ..paths import runtime_path
    from ..state import DestinationStateStore
    parser = argparse.ArgumentParser(description='Read pending Autumn result suggestions; never accepts a result')
    parser.add_argument('--state', default=str(runtime_path('autumn_results.json')))
    parser.add_argument('--event', default='autumn')
    parser.add_argument('--edition', default='2026')
    args = parser.parse_args()
    path = Path(args.state)
    if not path.exists():
        if path.with_name(path.name + '.unrecovered').exists():
            raise StateStoreError('Result receipts were quarantined; recover them before continuing')
        print('No result suggestions yet.')
        return
    destination = json.loads(path.read_text(encoding='utf-8'))['destination_key']
    store = DestinationStateStore(path.resolve(), destination, 'autumn_results', data_dir=path.resolve().parent)
    for key, entry in store.load().items():
        if not key.startswith('{}-{}|'.format(args.event, args.edition)):
            continue
        print('{} {} {}'.format(key, entry['status'], entry['room']))
        print(command_for(key, entry, event=args.event) or entry['reason'] or 'Waiting for finish')


if __name__ == '__main__':
    main()
