# SahasrahBot Z1R Integration Design

**Date:** 2026-02-26
**Status:** Approved

## Context

SahasrahBot provides seed rolling for all Z1R race rooms on racetime.gg, including TTP Season 4
races. SahasrahBot is currently experiencing downtime. TTPBot needs to act as a full stand-in for
SahasrahBot's Z1R features when SahasrahBot is offline — and is a candidate for full replacement
pending discussion with the Z1R Admin Team.

This design keeps TTPBot scoped to TTP Season 4 races for now. If SahasrahBot detection proves
reliable, expanding to all Z1R races can be assessed later.

## Approach

Approach A: inline implementation.

- `SEED_PRESETS` dict added to `config.py` (alongside existing `HASH_ALIASES`)
- Seed rolling commands added as `ex_*` methods on `TTPRaceHandler` in `handler.py`
- No new modules; two existing files change

## SahasrahBot Detection

TTPBot detects SahasrahBot's presence via its chat messages:

1. **On `begin()`:** Chat history is already requested via `gethistory`. In `chat_history()`,
   scan historical bot messages for any from a user whose name contains "sahasrahbot"
   (case-insensitive). If found, set `self.sahasrahbot_present = True`.

2. **On `chat_message()`:** Before the existing `is_bot: return` early exit, check if the
   incoming bot message is from SahasrahBot. If so, set `self.sahasrahbot_present = True`.

**Effect:**
- SahasrahBot online → TTPBot ignores seed commands entirely
- SahasrahBot offline → TTPBot responds to all seed commands as SahasrahBot would
- SahasrahBot comes back mid-race → first bot message from it flips the flag; TTPBot steps down

Informational commands (`!schedule`, `!info`, `!ttpflags`, `!help`) are **not gated** —
TTPBot responds to those regardless of SahasrahBot's presence.

## Commands

All new commands gated by `sahasrahbot_present` check (seed commands only):

| Command | Behavior |
|---|---|
| `!race <preset>` | Look up preset in `SEED_PRESETS`; if found, roll seed; if not, reply with available presets |
| `!flags <flagstring>` | Accept arbitrary flag string, roll and output seed |
| `!ttp4` | Random pick from TTP4 presets (RP, Hopla, Consternation), roll and output |
| `!ttp4rp` | Roll Consternation RP preset directly |
| `!ttp4hopla` | Roll Hopla preset directly |
| `!ttp4consternation` | Roll Consternation preset directly |
| `!help` | List all TTPBot commands (seed + informational; always available) |

Existing commands unchanged: `!schedule`, `!info`, `!ttpflags`.

## Seed Rolling

Matches SahasrahBot's Z1R implementation exactly:

```python
import random
seed = random.randint(0, 8999999999999999999)
```

**Chat output:**
```
Seed: 851187228554757722 - Flags: oIbnPfPb01Hll3D295IGDxxjR4UwfEok8P4MD
```

**Race info update:** Append seed+flags to `info_bot`. If a scheduled time string is already
present (from room creation), append: `| Seed: {seed} - Flags: {flags}`.

## Seed Locking

`self.seed_rolled = False` added to handler state.

- When TTPBot rolls a seed, set `self.seed_rolled = True`
- Subsequent seed commands respond: "A seed has already been rolled for this race."
- No override/reseed command (YAGNI)
- Only applies when TTPBot is acting as stand-in (SahasrahBot absent)

## Presets

`SEED_PRESETS` dict added to `config.py`. Full parity with SahasrahBot's 35 current Z1R presets.
TTP2 and TTP3 presets are omitted for now — the Z1R client app was updated and their flagstrings
have changed; they will be added back once correct flagstrings are confirmed.

```python
TTP4_PRESETS = ['ttp4rp', 'ttp4hopla', 'ttp4consternation']  # for !ttp4 random pick
```

## !help Output

```
TTPBot commands:
  Seed rolling (when SahasrahBot is offline):
    !race <preset>              Roll a seed by preset name
    !flags <flagstring>         Roll a seed with a custom flag string
    !ttp4                       Random TTP4 Season 4 preset
    !ttp4rp / !ttp4hopla / !ttp4consternation  TTP4 presets directly
  Season info:
    !schedule                   Today's remaining race times
    !info                       Season info
    !ttpflags                   TTP Season 4 flagset details
```

## Files Changed

| File | Change |
|---|---|
| `ttpbot/config.py` | Add `SEED_PRESETS` dict, `TTP4_PRESETS` list |
| `ttpbot/handler.py` | Add `sahasrahbot_present`, `seed_rolled` state; update `chat_history()` and `chat_message()` for detection; add `ex_race`, `ex_flags`, `ex_ttp4`, `ex_ttp4rp`, `ex_ttp4hopla`, `ex_ttp4consternation`, `ex_help` methods |

## Out of Scope

- Expanding to non-TTP Season 4 Z1R races (assess after tonight's detection results)
- TTP2 / TTP3 preset flagstrings (add when confirmed)
- Reseed / override command
- External API calls (Z1R seed generation is purely local)
