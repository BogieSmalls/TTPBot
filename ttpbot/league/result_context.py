"""Preserve the scheduled game when a room opens, across later sheet edits."""
from urllib.parse import urlsplit

from ..state import LEAGUE_GAMES


def bind_room(store, race, room):
    if store is None:
        return
    if not race.fixture or race.game not in LEAGUE_GAMES:
        raise ValueError('League room needs a fixture and Game 1 to 5')
    slug = urlsplit(room).path.rstrip('/').split('/')[-1]
    context = {
        'week': race.fixture.week,
        'game': race.game,
        'scheduledAt': race.start.isoformat(),
        'pairs': sorted(sorted([r.runner_one.sheet_name, r.runner_two.sheet_name])
                        for r in getattr(race, 'rows', (race,))),
    }
    saved = store.load()
    if slug in saved:
        if any(saved[slug][key] != context[key] for key in ('week', 'game', 'pairs')):
            raise ValueError('League room identity changed; Council must reconcile it')
        return
    saved[slug] = context
    store.save(saved)
