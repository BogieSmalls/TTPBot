# ZORA category — design

Date: 2026-11-17
Status: approved, implemented on `feat/zora-category`
Plan: `docs/superpowers/plans/2026-11-17-zora-category.md`

## Goal

Serve the RaceTime.gg `zora` category from TTPBot, so ZORA races get the seed
rolling, hash confirmation and chat logging z1r rooms already have, without a
separate bot. ZORA is a Zelda 1 Randomizer variant: each race has two flag
strings, the Z1R flags and the ZORA flags.

This replaces ZoraBot, a small standalone bot written for the first ZORA races.
ZoraBot's commands do not carry over; zora rooms use TTPBot's.

## Hard constraint: z1r is untouched

Everything a z1r category user can observe stays exactly as it is today: every
command, every reply, race info, the rooms TTPBot opens and joins, Discord
posts, state files, configuration, and the Council flag registry contract.
Council's published `flagsets.json` needs no change.

Concretely:

- No new `ex_` command on `TTPRaceHandler`. Zora rooms reuse `!flags`, `!race`,
  `!cancel`, `!summary` and `!help`, branching inside them only when the room
  is a zora room. `tests/test_flagset_registry.py` pins the handler's commands
  to Council's `reservedCommands`, and that list does not change.
- `tests/fixtures/flagsets-contract.json` is copied from Z1RR.Data and stays
  unchanged. `validate_snapshot(snapshot)` with no other argument behaves
  exactly as before; the ZORA rules are a separate mode,
  `validate_snapshot(snapshot, zora=True)`, with their own fixture.
- `deploy/ttpbot.env.example`, `deploy/ttpbot.service` and `--check-config`
  output are unchanged. The zora service has its own files.
- Every zora branch is entered only when the room's category is `zora` or the
  process serves the `zora` category. A z1r process never sees either.
- A z1r regression test (`tests/test_zora_z1r_unchanged.py`) drives z1r rooms
  through the same commands with a zora preset feed loaded, and checks that the
  replies and race info match today's.

## One process per category

`racetime_bot.Bot` serves one category per process, and TTPBot's state files are
keyed by `destination_key` (origin and category). The `zora` category therefore
runs as a second service, `ttpbot-zora.service`, from the same code, with
`TTPBOT_CATEGORY_SLUG=zora`, its own environment file, data directory and
scheduler lock.

### Zora mode

A process whose category is `zora` runs in zora mode (`ttpbot/zora.py`):

- **Rooms:** it joins every open room in the category, unlisted ones included.
  The `zora` category is ZORA's own; z1r's rule of joining only the rooms TTPBot
  scheduled exists because `z1r` is shared with the wider community.
- **Unlisted rooms:** the library's `refresh_races` reads the public
  `/<category>/data`, which leaves unlisted rooms out. RaceTime.gg's
  `/o/<category>/data` includes them for a token belonging to one of the
  category's bots. Zora mode lists rooms there and falls back to the public list
  if that call fails. z1r keeps the library's loop.
- **Nothing else runs:** no nightly room scheduler, League, Autumn, Corto, TTP
  tournament, grace minutes or nightly booths. It starts the ZORA preset feed
  and the room join loop only, as rehearsal mode starts only its own pieces.
- **Refusal:** like `TTPBOT_REHEARSAL`, zora mode refuses to start when a z1r
  feature is switched on (League, Autumn, Corto, TTP tournament, grace, nightly
  booths, rehearsal, a Discord webhook) or when `TTPBOT_DATA_DIR` is the z1r
  service's `/var/lib/ttpbot`. Each is a misconfiguration, never something to
  run half of.

## Commands in a zora room

TTPBot's commands, with an optional second flag string on `!flags`:

| Command | In a zora room |
|---|---|
| `!flags <flagstring> [zora flags]` | The Z1R string gets today's checks (a flag string, not a shortcut, long enough unless registered). The ZORA string is optional; without it the race has no ZORA flags. With it, it must be a ZORA flag string (`<version>.<payload>`, `_` read as `!`). |
| `!race <preset>` | A preset from the ZORA preset feed. |
| `!<shortcut>` | Any shortcut in the ZORA preset feed, as `!consternation` works in z1r. |
| `!cancel` | Today's: clears the roll so a new one can be rolled. |
| `!summary [flagstring\|preset]` | Today's, on the Z1R string; a preset name resolves through the ZORA feed. |
| `!help` | The commands above. |

Every other command (`!ttp5`, `!leagueweek3`, `!tc34`, `!schedule`, `!info`,
`!ttpflags`, `!matchup`, `!z1rr`, `!optout`, `!optin`, `!grace`, `!ttpbot`) is
silent in a zora room. Replies reuse TTPBot's wording ("A seed has already been
rolled for this race.", "Seed rolling complete.  See race info for details.",
"Seed cleared. You may now roll a new one.").

### Race info

```
Seed: 8368178622449674251 - Flags: 143oNtDD4PAvB!U8nSk4mxFW0br2w5YUspe7sx - ZORA: 13.7Zg4ZxiL - https://zora.gg/?flags=143oNtDD4PAvB%21U8nSk4mxFW0br2w5YUspe7sx&zora=13.7Zg4ZxiL&seed=8368178622449674251
```

- `Seed: N - Flags: X` is the order `!flags` writes in z1r.
- The ZORA string is labelled `ZORA:`, not `ZORA Flags:`, because TTPBot's
  `Flags: (\S+)` readers would also match the latter. With no ZORA string it
  reads `ZORA: none`.
- The zora.gg link opens the generator with both strings and the seed:
  `flags`, then `zora` (left out when empty), then `seed`, in the order zora.gg
  writes its own links. RaceTime.gg shows it as a link.
- Zora rooms are opened by people, not TTPBot, so there is no scheduling prefix
  to keep: the bot writes race info whole, and a roll after `!cancel` replaces
  the last one.

### After a restart

z1r reads chat history and stops at the first "Seed rolling complete.". A zora
room reads it with `tournament_room.roll_state`, where the last bot word wins,
so a restart after a `!cancel` does not lock the room.

### Welcome

A zora room gets its own welcome in place of the generic one, recognised in
history so it is said once.

## ZORA presets

zora.gg publishes `presets.json` from ZORA 2.0.1, in the format of Council's
`flagsets.json`, read by the same `FlagsetRegistry` class:

```json
{"schemaVersion": 1, "revision": 7, "updatedAt": "2026-11-17T20:00:00.000Z", "flagsets": [
  {"name": "All Swords No Boards", "description": null, "shortcuts": ["asnb"],
   "strings": [{"flags": "8hq4BeR1JXo7yjOOHjbxryVQW!UJ3A", "zora": "4.T9Jvg", "isLatest": true}]}
]}
```

The ZORA rules, applied only by `validate_snapshot(snapshot, zora=True)`:

- A string entry may carry `zora`: absent, `""`, or a ZORA flag string.
- A flag string is unique as the pair `(flags, zora)`: the same Z1R string may
  appear in two flagsets with different ZORA strings (Zora The Explorer weeks 1
  and 2 do).

Everything else is Council's contract as it stands. A Council file has no `zora`
key, so it means the same in either mode.

Settings, in `deploy/ttpbot-zora.env.example`:

- `ZORA_PRESETS_URL`: the feed. Blank keeps the built-in `ZORA_PRESETS` in
  `config.py` (ZoraBot's presets), as a blank `Z1RR_FLAGSETS_URL` keeps
  `SEED_PRESETS`.
- `ZORA_PRESETS_CACHE`: the last good copy; defaults to
  `$TTPBOT_DATA_DIR/zora_presets.json`.

The feed is polled every five minutes, checked whole, and cached, as Council's
is. Once a copy has loaded, the built-ins no longer apply.

## Out of scope

- Decoding the ZORA string field by field against zora.gg's flags spec
  (`https://zora.gg/flags/latest.json`, from 2.0.1). The format check catches
  typos of the shape; a full decode can follow once the spec is published.
- Listing unlisted z1r rooms through `/o/z1r/data`, and the z1r restart lock
  after a `!cancel`. Both would change z1r behaviour, so neither belongs here.
- Opening zora rooms on a schedule, Discord announcements, or results.
