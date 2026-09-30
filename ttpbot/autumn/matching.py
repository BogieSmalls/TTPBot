"""Which match a schedule row is about.

The Schedule tab names two racers and a time. It does not name a match, and two
racers are not enough to identify one:

  * the grand final and its reset are the same two people, back to back;
  * so is a losers-bracket rematch between a pair who met earlier;
  * and a best-of series is the same pair over several games.

So the row is resolved against the bracket, and everything downstream is keyed
by what the engine calls the match -- competition edition, match id, game -- not
by the pair. A pair is how a human writes it down; it is not an identity.

Nothing here talks to anything. It takes a bracket snapshot and some rows and
says which match each row means, or why it cannot tell.
"""

from dataclasses import dataclass, field
from typing import Optional


def flatten(name):
    """A name reduced to what two spellings of it have in common.

    The draw, the rankings and the form's dropdowns were typed weeks apart, and
    they disagree about case and punctuation often enough that it has already
    cost a racer their ranking on the page: `eatmysteel` against `Eatmysteel`,
    `Droois` against `droois`. Comparing flattened is the only comparison that
    has held.
    """
    return ''.join(
        character
        for character in str(name or '').lower()
        if character.isalnum()
    )


def strip_prefix(name):
    """`(5) Bogie` -> `Bogie`.

    The scheduling form's dropdowns carry the tournament ranking as a prefix, so
    that is what lands on the sheet. The League has the same shape with a team
    abbreviation and strips it the same way.
    """
    text = str(name or '').strip()
    if text.startswith('('):
        closing = text.find(')')
        if closing != -1:
            return text[closing + 1:].strip()
    return text


@dataclass(frozen=True)
class RaceIdentity:
    """What the engine calls a race: an edition, a match, and a game in it.

    Deliberately not the pair of racers, and deliberately not the start time.
    The time is the one part of a race that changes, and keying anything by it is
    how a postponed race gets a second room -- which the League runner did until
    today.
    """

    event: str
    match_id: str
    game: int = 1

    @property
    def key(self):
        return '{}|{}|{}'.format(self.event, self.match_id, self.game)


@dataclass
class Unresolved:
    """A row the bracket cannot account for, and why.

    Its own type because these must be *reported*, not dropped. A row nobody can
    place is two racers who think they are scheduled, and silence there is worse
    than a wrong guess -- somebody can fix a complaint.
    """

    reason: str
    runner_one: str
    runner_two: str
    at: Optional[object] = None


@dataclass
class Resolved:
    identity: RaceIdentity
    match_id: str
    at: object
    runner_one: str
    runner_two: str
    #: True when the match cannot be raced yet even though it is scheduled.
    #: The reset is the case that matters: it is schedulable the moment the
    #: finalists are known, and raceable only if the final goes a certain way.
    conditional: bool = False
    why_conditional: Optional[str] = None


@dataclass
class Matching:
    resolved: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)

    def raceable(self):
        """The ones a room may be opened for now."""
        return [race for race in self.resolved if not race.conditional]


def _pair(match):
    return frozenset((flatten(match.get('a')), flatten(match.get('b'))))


def _finalists(matches):
    """The two in the grand final, once both are known."""
    final = matches.get('GF-1')
    if not final or not final.get('a') or not final.get('b'):
        return None
    return _pair(final)


def _reset_needed(matches):
    """Whether GF-2 definitely has to be played.

    The engine's own answer is used rather than the reset rule reimplemented
    here -- two copies of that rule would eventually disagree about who won a
    tournament. But its state needs reading carefully, because one word covers
    two situations:

        at the very start       GF-1: waiting   GF-2: not-needed
        with the final ready    GF-1: ready     GF-2: not-needed
        final won by LB side    GF-1: played    GF-2: ready

    `not-needed` means "not needed *yet*" before the final and "not needed
    *ever*" after it. So the final's own state is what separates them, and
    "needed" means: the final has been played, and the reset was not ruled out.
    Anything else is undetermined, which is a reason to hold a room rather than
    to refuse a time.
    """
    final = matches.get('GF-1') or {}
    if final.get('state') != 'played':
        return False
    reset = matches.get('GF-2')
    if not reset:
        return False
    return reset.get('state') not in ('not-needed', None)


def match_rows(rows, matches, *, event='autumn'):
    """Resolve schedule rows against a bracket snapshot.

    `rows` are dicts of `at`, `runner_one`, `runner_two` -- names as the sheet
    spells them, prefix and all. `matches` is the public draw's matches keyed by
    id, each with `a`, `b` and `state`.

    Rows are taken in time order, because that is what separates the two finals
    when both are scheduled: the earlier is the final, the later is the reset.
    """
    by_id = dict(matches)
    ordered = sorted(
        rows,
        key=lambda row: (row.get('at') is None, row.get('at')),
    )

    finalists = _finalists(by_id)
    finals_rows = []
    out = Matching()

    for row in ordered:
        one = strip_prefix(row.get('runner_one'))
        two = strip_prefix(row.get('runner_two'))
        pair = frozenset((flatten(one), flatten(two)))

        if len(pair) < 2:
            out.unresolved.append(Unresolved(
                'the two racers are the same person', one, two, row.get('at')))
            continue

        # The finals are handled together, after every row is seen, because the
        # answer for one depends on whether the other was scheduled too.
        if finalists is not None and pair == finalists:
            finals_rows.append((row, one, two))
            continue

        candidates = [
            match_id for match_id, match in by_id.items()
            if match_id not in ('GF-1', 'GF-2') and _pair(match) == pair
        ]
        playable = [
            match_id for match_id in candidates
            if by_id[match_id].get('state') in ('ready', 'waiting')
        ]

        if not candidates:
            out.unresolved.append(Unresolved(
                'no match in the bracket has these two racers', one, two, row.get('at')))
            continue
        if not playable:
            out.unresolved.append(Unresolved(
                'every match with these two racers has already been played',
                one, two, row.get('at')))
            continue
        if len(playable) > 1:
            # Should not happen with one game per match, and must be said rather
            # than guessed if it ever does.
            out.unresolved.append(Unresolved(
                'more than one match is waiting for these two racers: {}'.format(
                    ', '.join(sorted(playable))),
                one, two, row.get('at')))
            continue

        match_id = playable[0]
        out.resolved.append(Resolved(
            identity=RaceIdentity(event=event, match_id=match_id),
            match_id=match_id,
            at=row.get('at'),
            runner_one=one,
            runner_two=two,
            conditional=by_id[match_id].get('state') != 'ready',
            why_conditional=(
                None if by_id[match_id].get('state') == 'ready'
                else 'the match is not raceable yet'
            ),
        ))

    out.resolved.extend(_match_finals(finals_rows, by_id, event))
    return out


def _match_finals(finals_rows, by_id, event):
    """Assign rows for the finalists to GF-1 and GF-2.

    Four cases, and the only one that needs care is the last:

      * two rows -> the earlier is the final, the later is the reset
      * one row, the final unplayed -> the final
      * one row, the final played and a reset needed -> the reset
      * one row, the final played and no reset -> nothing to race

    A reset is *schedulable* long before it is raceable -- the finalists agree
    both times at once, because nobody wants to be arranging a second race at
    one in the morning. So it is resolved and marked conditional rather than
    refused, and the runner holds off opening its room until the final says the
    reset is happening.
    """
    if not finals_rows:
        return []

    final = by_id.get('GF-1') or {}
    played = final.get('state') == 'played'
    reset_needed = _reset_needed(by_id)

    def resolved(row, one, two, match_id, conditional, why=None):
        return Resolved(
            identity=RaceIdentity(event=event, match_id=match_id),
            match_id=match_id,
            at=row.get('at'),
            runner_one=one,
            runner_two=two,
            conditional=conditional,
            why_conditional=why,
        )

    if len(finals_rows) >= 2:
        first, second = finals_rows[0], finals_rows[1]
        out = [resolved(first[0], first[1], first[2], 'GF-1', conditional=played)]
        out[0].why_conditional = 'the final has already been played' if played else None
        out.append(resolved(
            second[0], second[1], second[2], 'GF-2',
            conditional=not reset_needed,
            why=None if reset_needed else 'the reset happens only if the final calls for it',
        ))
        return out

    row, one, two = finals_rows[0]
    if not played:
        return [resolved(row, one, two, 'GF-1', conditional=False)]
    if reset_needed:
        return [resolved(row, one, two, 'GF-2', conditional=False)]
    return []
