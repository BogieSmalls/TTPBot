# Z1RR League race automation — Phase 1 design

Date: 2026-09-02
Status: approved, not yet implemented
Research handoff: `Z1RR.Restream` PR #97,
`docs/superpowers/plans/2026-09-02-z1rr-league-race-automation.md`

## Goal

Open a racetime.gg race room for every Z1RR League match on the League
schedule spreadsheet, invite the two racers, and announce the room in Discord —
without anyone hand-creating rooms.

Phase 1 is room creation, racer invites, and the Discord post. It applies to
every scheduled League race, restream or not, and does not touch
`Z1RR.Restream`.

Phase 2 (control-plane wake, booth creation, crew invites) is out of scope and
is deliberately deferred until Phase 1 has proven the schedule data is
trustworthy.

## Hard constraint: TTP is untouched

Nothing in this work may change the behavior of Triforce Triple Play
scheduling. TTP Season 5 is live in production on the same bot process, and
League is strictly additive.

Concretely, this design guarantees:

- No change to `WEEKLY_SCHEDULE`, `SEASON_START`/`SEASON_END`, `RACE_NUMBER_MAP`,
  `REMINDER_SCHEDULE`, or any TTP goal or room-info constant.
- No change to `created_races.json` or `sent_webhooks.json` — neither their
  schema, their contents, nor their `schema_version`. League uses separate
  files.
- No change to TTP's Discord webhook, its role ping, or its T-20 timing.
- No modification of `TTPBot._create_race_room`, `_recover_uncertain_room`,
  `_send_webhook`, `_check_and_create_races`, or `race_room_form_data`.
- No change to reminders, seed rolling, hash detection, or chat logging as they
  behave in TTP rooms today.

Only three edits touch existing TTP-shared files, and all three are additive:
one `or` clause in `should_handle()`, one extra `create_task()` in `run()`, and
a branch in the handler that is entered only when League state is present.

An earlier draft proposed extracting TTP's `startrace` POST and uncertain-response
recovery into a helper shared with League. That is dropped. The small duplication
of a room-creation function inside the League module is worth having zero
refactoring risk in live TTP code.

## Corrections to the research handoff

Two claims in the handoff do not survive contact with the current code. Both
were verified on 2026-09-02.

1. **The proposed idempotency key cannot be stored.** The handoff proposes
   keying on date + time + both runners. `DestinationStateStore._validate_key`
   requires every key to parse as `datetime.fromisoformat()` and to be
   timezone-aware, and `ENTRY_KINDS` is a closed set of `created_races` and
   `sent_webhooks`. League state therefore cannot use the existing store
   unchanged.

   This matters because concurrent races are normal, not exceptional: the live
   sheet has two matches at 9/3/2026 8:00 PM (`seanfreston vs Stags28` and
   `SirLinkalot vs Windfox470`). League nights run 1-on-1 and 2-on-2 formats, so
   multiple rooms in one slot are expected. A timestamp-only key would collide
   and the second room would silently never open.

2. **Creating the room is not enough to invite anyone.** `invite_user(hashid)`
   is confirmed unconditional in the installed `racetime_bot` (it sends
   `{"action": "invite"}` with no invitational precondition), but it is a
   `RaceHandler` method — it exists only after the bot has joined the room over
   the websocket. Joining is gated by `Bot.should_handle()`, which TTPBot
   narrows to `is_ttp_scheduled_room()`. A League room's goal is `Beat the game`,
   which is exactly `POST_SEASON_GOAL_NAME`, but its `info_bot` will not carry a
   TTP prefix, so the policy returns `False`. Without a room-policy change,
   rooms would be created and then never joined, and no invite would ever fire.

The handoff's room title is also superseded: rooms are titled
`League: X vs. Y`, not `Z1RR League - X vs. Y`.

## Decisions

| decision | choice |
|---|---|
| Room title (`info_bot`) | `League: X vs. Y` |
| Goal | `Beat the game` |
| `invitational` | `false` — open, so commentators and trackers can join |
| Behavior in room | invite both racers; seed rolling with SahasrahBot deferral; **no reminders** |
| Racer identity source | JSON roster committed to this repo |
| Discord destination | new webhook to `#league-scheduling`, mentions both racers |
| Room opens | T-30 |
| Discord post | T-30, immediately after the room is created |
| `Game` column | ignored in Phase 1 |
| `Comms` / `Tracker` columns | ignored in Phase 1; Phase 2 concern |

## Architecture

League runs as a parallel subsystem inside the existing TTPBot process, not as
a generalization of the existing scheduler.

The alternative — refactoring the scheduler around a single `ScheduledRace`
abstraction serving both TTP and League — is the better end state but the wrong
Phase 1, for the reason given under the hard constraint above. League instead
gets its own module, its own state store, and its own scheduler task, sharing
only the process, the destination (`racetime.gg` / `z1r`), the OAuth
credentials, and the handler class.

### New modules

| file | responsibility |
|---|---|
| `ttpbot/league/__init__.py` | package marker |
| `ttpbot/league/roster.py` | load and index the roster; resolve a sheet name to a `Racer` |
| `ttpbot/league/roster.json` | the 42 committed racer records |
| `ttpbot/league/schedule.py` | fetch and parse the schedule CSV into `LeagueRace` values |
| `ttpbot/league/rooms.py` | League room form data and room creation, including uncertain-response recovery |
| `ttpbot/league/scheduler.py` | the T-30 loop: create room, seed handler state, announce |
| `ttpbot/league/announce.py` | the Discord post and its allow-list |

### Modified modules

| file | change | additive? |
|---|---|---|
| `ttpbot/room_policy.py` | add `is_league_room()` | new function only |
| `ttpbot/bot.py` | `should_handle()` gains an `or is_league_room(...)`; `run()` gains one `create_task()`; League stores constructed when enabled | yes |
| `ttpbot/handler.py` | League branch — invite on `begin()`, suppress reminders | entered only when League state present |
| `ttpbot/state.py` | two new entry kinds; `_validate_key` dispatches on kind | existing kinds keep current behavior |
| `ttpbot/runtime_config.py` | League webhook, enable flag, schedule URL | new optional fields |
| `ttpbot/__init__.py` | League CLI flags | new optional flags |
| `deploy/ttpbot.env.example` | document the three new variables | additive |

## Data model

```python
@dataclass(frozen=True)
class Racer:
    sheet_name: str      # "Droois" — as written in the sheet, prefix stripped
    team: str            # "SC"
    display_name: str    # "Droois" — what appears in the room title
    racetime_id: str     # "NqO2YoLLAbo9QEya"
    discord_id: str | None

@dataclass(frozen=True)
class LeagueRace:
    start: datetime      # timezone-aware, US/Eastern
    runner_one: Racer
    runner_two: Racer
    channel: str | None  # restream channel, Phase 2 input; carried, unused
```

### Roster file

`ttpbot/league/roster.json` is a list of objects with the `Racer` fields. It is
the single source of truth for identity in Phase 1: no live lookup happens at
T-30, so a cold racetime API or a renamed account cannot break room creation
minutes before a race. A racer who changes accounts is a roster edit and a
redeploy.

The racetime hashids are recorded in the handoff document and are copied in
verbatim. **The Discord ids are not in the handoff** — coverage was verified as
42/42 against production but the values were not recorded, so building the
roster file requires one export from `Z1RR.Restream`:
`SELECT display_name, twitch_channel, discord_id FROM crop_profiles WHERE
league_team IS NOT NULL`. This is a read, and is the only cross-repo dependency
in Phase 1.

Name resolution is exact, case-insensitive, against `sheet_name` after stripping
the `(TEAM) ` prefix. Six roster entries exist specifically because the sheet
name differs from the crop profile (`Deus Ex Machina`, `Birdman`, `Merks`,
`Sigil`, `BlessedBe`, `Droois`); the roster records the sheet spelling directly,
so no fuzzy matching is needed or permitted at runtime. `Droois` in particular
must resolve to `grindhalo` and never `droorace`.

## Schedule source

```
https://docs.google.com/spreadsheets/d/1MEyO03Wib6iyH7-75e-orh2K75AATwEoJB9HlTe9VgM/export?format=csv&gid=2033319762
```

Public, unauthenticated CSV; no service account. Columns:
`Date, Time, Game, Runner 1, Runner 2, , Comms, Tracker, , Channel, Booth`.
Dates are `M/D/YYYY`, times are `H:MM:SS AM/PM` in US/Eastern, and runner cells
carry a `(TEAM) ` prefix that is stripped before lookup.

Parsing is total and defensive. A row that cannot be parsed or whose runners
cannot be resolved is skipped with a logged reason and does not stop the other
rows. The sheet is a live document and every cell is untrusted input.

## Idempotency

League state keys are `<iso_start>|<slug>`:

```
2026-09-03T20:00:00-04:00|sirlinkalot-vs-windfox470
```

The slug is built from the two resolved `sheet_name` values, lowercased, with
non-alphanumerics collapsed to `-`, and joined in **sorted order** — not sheet
column order.

Sorting matters: if someone edits the sheet and swaps the two runner columns,
a column-ordered key would change and the scheduler would open a second room
for a match that already has one. Sorting makes the key depend only on *which
two racers* are in the match, which is the actual identity of the race.

The room title, by contrast, uses sheet column order, so `League: X vs. Y`
reads the way the schedule reads. Title order and key order are deliberately
independent.

The timestamp prefix keeps `cleanup_before()` working unchanged, since it needs
only `datetime.fromisoformat(key.split('|', 1)[0])`. ISO-8601 timestamps never
contain `|`, so the split is unambiguous.

`state.py` gains entry kinds `league_created_races` and `league_sent_webhooks`,
and `_validate_key` dispatches on entry kind — timestamp-only for the existing
TTP kinds, `<iso>|<slug>` for the League kinds. The existing kinds keep exactly
their current validation. Every other property of the store is inherited
unchanged: atomic replace, `fsync`, symlink refusal, `TTPBOT_DATA_DIR`
containment, corrupt-file quarantine, destination binding, and the size cap.

State lives in `league_races.json` and `league_webhooks.json`, separate files
from the TTP stores, so a League bug cannot corrupt TTP scheduling state.

## Room recognition

```python
def is_league_room(race_data):
    goal = race_data.get('goal', {}).get('name', '')
    info = race_data.get('info_bot', '') or ''
    return goal == POST_SEASON_GOAL_NAME and info.startswith(LEAGUE_ROOM_INFO_PREFIX)
```

with `LEAGUE_ROOM_INFO_PREFIX = 'League: '`. `Bot.should_handle()` becomes
`is_ttp_scheduled_room(...) or is_league_room(...)`.

The two predicates share the `Beat the game` goal and are separated purely by
the `info_bot` prefix. TTP post-season rooms use
`'<TTP prefix> | Scheduled:'`; League rooms use `'League: '`. These cannot
overlap, and the separation is asserted in both directions by test.

The wider constraint still holds: `z1r` is shared with the Z1R community, and
the bot handles only rooms it scheduled. `is_league_room` narrows on a title
this automation itself writes, so it cannot capture a community room.

## Handler behavior

When the League scheduler creates a room it pre-seeds the bot's per-race state
before the handler exists:

```python
bot.state.setdefault(race_name, {})['league_race'] = {
    'invite': [runner_one.racetime_id, runner_two.racetime_id],
    'title': 'League: X vs. Y',
}
```

`Bot.create_handler` does `if race_name not in self.state: self.state[race_name] = {}`
and passes that dict into the handler by reference as `state`, so a pre-seeded
entry survives and arrives at the handler with no override of `create_handler`.
`race_name` is the room path (`z1r/<slug>`), derived from the `Location` header
that `provider.resolve_location()` already validates.

On `begin()`, a handler whose state carries `league_race`:

- calls `invite_user()` for each hashid, once, guarded by a flag set before the
  awaits so a reconnect cannot double-invite;
- **skips reminder scheduling entirely** — no 10/5/1/0 minute messages, and no
  call into `find_nearest_scheduled_race()`, which is hardcoded to the TTP
  weekly schedule and would be wrong here;
- keeps seed rolling, SahasrahBot deferral, `!help`, hash detection, and chat
  logging exactly as they behave today.

A handler whose state has no `league_race` key takes the existing TTP path
unchanged.

If the process restarts between room creation and joining, the seeded state is
gone. The handler then falls back to parsing `X vs. Y` out of the room's
`info_bot` and re-resolving both names through the roster. Invites are therefore
durable across a restart. If that fallback cannot resolve both names, it logs
and invites nobody rather than guessing.

## Discord announcement

A second webhook, configured independently of the existing TTP one:

- `TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL` — validated by the existing
  `_validate_webhook` rules (https, `discord.com`, `/api/webhooks/<id>/<token>`).

Content:

```
League: <@id1> vs <@id2> — <race_url>
```

```json
{"allowed_mentions": {"parse": [], "users": ["<id1>", "<id2>"]}}
```

The allow-list is explicit and contains exactly the two racer ids. Message
content is assembled from a live spreadsheet, so `parse` is empty and nothing
else can ping: a stray `@everyone` in a cell is inert. A racer with no
`discord_id` is rendered as their plain display name rather than a broken
`<@>` mention, and their id simply does not enter the allow-list.

The post fires at T-30, immediately after the room is created, and is recorded
in `league_webhooks.json` under the same key as the room so it is sent once and
survives restarts.

League announcements are configured separately from TTP announcements: if the
League webhook is absent, League room creation and invites still run, and the
announcement is skipped with a warning. TTP's webhook and role ping are
untouched.

## Failure handling

| failure | behavior |
|---|---|
| CSV fetch fails | reuse the last good snapshot, up to a 6-hour age cap; past that, create no League rooms and log an error each tick |
| Row unparseable | skip the row, log the reason, continue other rows |
| Racer name unresolvable | skip the race, log both names, never guess — wrong racers in a live race is worse than no room |
| `startrace` non-201 | log status, no state written, retried next tick |
| Creation times out | persist `UNCERTAIN_RACE` fail-closed, then attempt recovery by matching `info_bot` exactly against `League: X vs. Y` in `/z1r/data`; never blindly re-POST |
| Discord post fails | logged; does not affect the room or the invites |
| Roster file missing or invalid | League scheduler refuses to start and logs; TTP scheduling is unaffected |
| Any unhandled League exception | caught at the League scheduler loop boundary and logged, exactly as TTP's loop does, so it cannot kill the shared process |

The 6-hour cap is the deliberate middle ground between two bad outcomes: a
transient Google outage should not cost a scheduled race, and a sheet that has
silently stopped being reachable should not keep opening rooms from data nobody
can see any more.

## Configuration

| variable | meaning |
|---|---|
| `TTPBOT_LEAGUE_ENABLED` | `true`/`false`; off by default so deploying this code changes nothing until switched on |
| `TTPBOT_LEAGUE_SCHEDULE_URL` | schedule CSV URL; defaults to the published sheet |
| `TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL` | announcement webhook |

`deploy/ttpbot.env.example` gains all three. `--check-config` reports whether
League is enabled without contacting the network.

Because the flag defaults off, the first deploy of this code is a no-op for the
running service: TTP continues exactly as today until League is explicitly
switched on.

## Testing

Test-driven, following the existing `tests/` layout and fakes.

**Schedule parsing** — `(TEAM) ` prefix stripping; `M/D/YYYY` and 12-hour times;
Eastern DST boundaries in both directions; two races in one slot both surviving
as distinct races; blank `Channel` and `Comms`; malformed rows skipped without
affecting neighbours; a runner not on the roster skipping only that race.

**Roster** — exact case-insensitive resolution; all six divergent names;
`Droois` resolving to `grindhalo` and never `droorace`; unknown name raising
rather than fuzzy-matching; missing `discord_id` tolerated.

**Keys and state** — slug stability across ticks and across runner order in the
sheet; two same-slot races producing different keys; new entry kinds
round-tripping; `cleanup_before()` correctly ageing out League keys; a League
key rejected by a TTP store and vice versa; destination binding still enforced.

**Room policy** — a League room handled; a TTP season room handled; a TTP
post-season room *not* matched as League; a League room *not* matched as TTP; an
unrelated community `Beat the game` room handled by neither.

**Handler** — invites fire once for both racers; a second `begin()` does not
re-invite; reminders are not scheduled in a League room; reminders *are* still
scheduled in a TTP room; seed rolling and SahasrahBot deferral still work; the
`info_bot` fallback recovers invites when seeded state is missing; unresolvable
fallback invites nobody.

**Discord** — payload allow-lists exactly the two ids; `parse` is empty; an
`@everyone` in a runner cell cannot produce a mention; missing `discord_id`
degrades to a display name; webhook recorded once per race.

**Uncertain creation** — timeout persists the marker; recovery matches only an
exact `League: X vs. Y`; ambiguity resolves to no room rather than a guess.

**TTP regression** — the existing suite passes unchanged, and League disabled
produces byte-identical TTP scheduling behavior.

## Out of scope

Control-plane wake, booth creation, crew invites, `Comms`/`Tracker` name
resolution, the `Game` column, any `Z1RR.Restream` schema change, and any
generalization of the TTP scheduler.
