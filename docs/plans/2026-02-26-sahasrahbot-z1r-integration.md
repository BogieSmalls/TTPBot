# SahasrahBot Z1R Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Z1R seed-rolling commands to TTPBot so it acts as a full stand-in for SahasrahBot
when SahasrahBot is offline, with automatic SahasrahBot detection.

**Architecture:** Two existing files change: `config.py` gains a `SEED_PRESETS` dict, and
`handler.py` gains SahasrahBot detection logic + seed-rolling command handlers. Seed generation
is `random.randint(0, 8999999999999999999)` — no external API. TTPBot silently defers all seed
commands when it detects SahasrahBot is present in the room.

**Tech Stack:** Python 3.9+, `racetime_bot` framework, `asyncio`, `random` (stdlib)

---

## Background: How SahasrahBot's Z1R seed rolling works

SahasrahBot has two code paths:

**`!flags <flagstring>` → `roll_game()`:**
- Chat: `"Seed: {seed} - Flags: {flags}"`
- Race info: `"Seed: {seed} - Flags: {flags}"`
- Chat: `"Seed rolling complete.  See race info for details."`

**`!race <preset>` → `ex_race()`:**
- Chat: `"{preset} - Flags: {flags} Seed: {seed_number}"`
- Race info: `"Flags: {flags} Seed: {seed_number}"`
- Chat: `"Seed rolling complete.  See race info for details."`

We match these formats exactly so racers see identical output.

**SahasrahBot detection:** Watch for bot messages from a user whose name contains
"sahasrahbot" (case-insensitive). Check both chat history (on connect) and live messages.

**Race info preservation:** TTPBot's bot info already contains `"TTP Season 4 | Scheduled: ..."`.
When adding seed info, preserve the scheduling prefix rather than replacing everything.

---

## Task 1: Add SEED_PRESETS to config.py

**Files:**
- Modify: `ttpbot/config.py`

**Step 1: Add the presets dict and TTP4 list**

Append to the bottom of `ttpbot/config.py`:

```python
# Named seed presets for Z1R (from SahasrahBot parity).
# TTP2 and TTP3 presets omitted until correct flagstrings are confirmed
# (the Z1R client app was updated and the old flagstrings changed).
SEED_PRESETS = {
    'abns22_swiss':      'oIbnPfPb01HJEAN8LBIMmlBWz!gGeqNjpYphk',
    'abns22_bracket':    '143oNtDD4RQLqGPLpaSfjC2AnRmLOpqFAW5QT4',
    'abns22_top8':       '143oNtDD4RQLrJy5BfGRUWmqGpjvyjX29Xj97o',
    'consternation':     'oIbnPfPb01Hll3D29Bc2!etrojQOSjJQZUJ3A',
    '2019brackets':      'oIbnPfPaymqfyH4t7pgvRD4cP1H1I7sPTblX9',
    'rr2024':            'oIbq2JttN3ae9PzaCJOsVpXedaDD1C2B1yAt8',
    'sgl24':             'oIbnPfPb01HmodtgCWCvSuAbuHPqZRIVKCvKj',
    'bettypls':          'q9yBZxHDLh6lovXqLgSNrmEieda2tqJLXZUEo',
    'excavator':         'hvFx!4yyHzSQ4RIGBlC82EgnMP0hKMULyMo0WP',
    'jesscherk':         'oIbnPg!o7RsFZtKnEc9AAI8WI7bpnQVS16uNU',
    'magsrush':          'oIbnPfPb01Hll3F1P2W2uMZOcOJWr!jS05L0A',
    'babysfirsthdn':     'oVeI512duFh1JQJ6dol1rhfGazsxVWEBeazWF',
    'walkitin':          'oIbnPfPaymqfyH3OJhgiKHew0nr6Guj6lqHtv',
    'swordlessplus':     'oIbnPfPb01Hns9ilYf3aXrUwNv7SZU05a56UV',
    'randomforce':       'oIbnPfPazW!1troLaKovLCWdzL0Ech79PCP7x',
    'rr2025':            'CKnGZ6u7XaVW!hJ!sGTvkRim82t8PvIW1BEycZo',
    'sgl25online':       '12TDBJOu7zgjkBGTDwHViA9wS3IpdJcvhCVEtu9',
    'power':             '143oNtDD4PAvBt5G8xyCFu5kwp7tS8vUBVpiZY',
    'courage':           'oIbnQLMCpyScZbUVFbgpGKPLsHFflaoYKIxoA',
    'wisdom':            'oIbnPfPb0mR7ggY12zwI0QNIY620UnhU8kiC3',
    'sgl25ip':           '12TDBJOu7zgjkBGTDsoDP5Hg7jnJNmTUCh4wT9X',
    'ttp4rp':            '24hJoDaoq92qaumIfio4Qq8LtfU0Xt8tpG3Iafo',
    'ttp4hopla':         'oIbnRjuMUKqwdnOXzOMO7PuDtwAvU3boJnaXW',
    'ttp4consternation': 'oIbnPfPb01Hll3D295IGDxxjR4UwfEok8P4MD',
    'afbns_swiss':       '1K9hKZCQamJvAprO0CLKqHgZk0MR1RqiE9bfe9xv',
    'afbns_bracket':     '12V4XiZA!b3mWgwigt9JQcwZSUlpadoHsJNny3J',
    'afbns_top8':        '12V4XiZA!b3mWh!GQFcZr9rUkzjsoaFlYFS64EU',
}

# Presets used by !ttp4 random picker
TTP4_PRESETS = ['ttp4rp', 'ttp4hopla', 'ttp4consternation']
```

**Step 2: Verify the file looks right**

Run: `python -c "from ttpbot.config import SEED_PRESETS, TTP4_PRESETS; print(len(SEED_PRESETS), 'presets;', TTP4_PRESETS)"`

Expected output: `27 presets; ['ttp4rp', 'ttp4hopla', 'ttp4consternation']`

**Step 3: Commit**

```bash
git add ttpbot/config.py && git commit -m "feat: add SEED_PRESETS and TTP4_PRESETS to config"
```

---

## Task 2: Add state fields and SahasrahBot detection

**Files:**
- Modify: `ttpbot/handler.py`

**Step 1: Update imports at the top of handler.py**

The existing import block already imports from `.config`. Add `SEED_PRESETS` and `TTP4_PRESETS`
to that import:

```python
from .config import (
    HASH_ALIASES,
    HASH_ALIASES_MULTI,
    RACE_NUMBER_MAP,
    RECAP_WEBHOOK_URL,
    REMINDER_SCHEDULE,
    SEED_PRESETS,
    TTP4_PRESETS,
    TIMEZONE,
)
```

Also add `random` to the stdlib imports at the top of the file:

```python
import random
```

**Step 2: Add new state fields to `__init__`**

In `TTPRaceHandler.__init__`, after `self.recap_data = {...}`, add:

```python
        self.sahasrahbot_present = False
        self.seed_rolled = False
```

**Step 3: Add SahasrahBot detection helper**

Add this static method to `TTPRaceHandler` (before `begin()`):

```python
    @staticmethod
    def _is_sahasrahbot_msg(message):
        """Return True if this message is from SahasrahBot."""
        if not message.get('is_bot'):
            return False
        name = (message.get('user') or {}).get('name', '')
        return 'sahasrahbot' in name.lower()
```

**Step 4: Update `chat_history()` to detect SahasrahBot in history**

In `chat_history()`, add the detection check after the `messages = data.get('messages', [])` line.
Insert before the `bot_messages = [...]` comprehension:

```python
        # Detect SahasrahBot presence from chat history
        for msg in messages:
            if self._is_sahasrahbot_msg(msg):
                self.sahasrahbot_present = True
                self.logger.info(
                    '[%s] SahasrahBot detected in chat history — seed commands deferred',
                    self.data.get('name'),
                )
                break
```

**Step 5: Update `chat_message()` to detect SahasrahBot in live messages**

In `chat_message()`, the current code has:
```python
        if message.get('is_bot'):
            return
```

Replace that block with:
```python
        if message.get('is_bot'):
            if self._is_sahasrahbot_msg(message) and not self.sahasrahbot_present:
                self.sahasrahbot_present = True
                self.logger.info(
                    '[%s] SahasrahBot detected live — seed commands deferred',
                    self.data.get('name'),
                )
            return
```

**Step 6: Verify the imports and state load correctly**

Run: `python -c "from ttpbot.handler import TTPRaceHandler; print('OK')"` from
`D:\Projects\Streaming\TTPBot`.

Expected: `OK`

**Step 7: Commit**

```bash
git add ttpbot/handler.py && git commit -m "feat: add SahasrahBot detection and seed_rolled state"
```

---

## Task 3: Add seed-rolling helper and !flags / !race commands

**Files:**
- Modify: `ttpbot/handler.py`

These methods go at the end of `TTPRaceHandler`, after `ex_ttpflags`.

**Step 1: Add the `_roll_seed_raceinfo()` helper**

This helper preserves any scheduling prefix already in the bot race info, then appends
seed info — so we don't clobber the "TTP Season 4 | Scheduled: ..." string.

```python
    async def _roll_seed_raceinfo(self, seed_str):
        """Update race info, preserving any existing scheduling prefix."""
        current = self.data.get('info_bot', '') or ''
        # Strip any previously-written seed segment
        for marker in ('| Seed:', '| Flags:'):
            if marker in current:
                current = current[:current.index(marker)].rstrip()
        new_info = f'{current} | {seed_str}' if current else seed_str
        await self.set_bot_raceinfo(new_info)
```

**Step 2: Add `ex_flags()`**

Matches SahasrahBot's `roll_game()` behavior exactly:
- Chat: `"Seed: {seed} - Flags: {flags}"`
- Race info: `"Seed: {seed} - Flags: {flags}"`
- Chat: `"Seed rolling complete.  See race info for details."`

```python
    async def ex_flags(self, args, message):
        """!flags <flagstring> -- Roll a seed with a custom flag string."""
        if self.sahasrahbot_present:
            return

        if not args:
            await self.send_message('You must specify a set of flags!')
            return

        if self.seed_rolled:
            await self.send_message('A seed has already been rolled for this race.')
            return

        flags = args[0]
        seed = random.randint(0, 8999999999999999999)
        seed_str = f'Seed: {seed} - Flags: {flags}'

        await self._roll_seed_raceinfo(seed_str)
        await self.send_message(seed_str)
        await self.send_message('Seed rolling complete.  See race info for details.')
        self.seed_rolled = True
        self.logger.info('[%s] Seed rolled via !flags: %s', self.data.get('name'), seed_str)
```

**Step 3: Add `ex_race()`**

Matches SahasrahBot's `ex_race()` behavior exactly:
- Chat: `"{preset} - Flags: {flags} Seed: {seed}"`
- Race info: `"Flags: {flags} Seed: {seed}"`
- Chat: `"Seed rolling complete.  See race info for details."`

```python
    async def ex_race(self, args, message):
        """!race <preset> -- Roll a seed by named preset."""
        if self.sahasrahbot_present:
            return

        if self.seed_rolled:
            await self.send_message('A seed has already been rolled for this race.')
            return

        if not args:
            presets = ', '.join(sorted(SEED_PRESETS.keys()))
            await self.send_message(f'No preset specified. Available presets: {presets}')
            return

        preset = args[0].lower()
        if preset not in SEED_PRESETS:
            presets = ', '.join(sorted(SEED_PRESETS.keys()))
            await self.send_message(
                f'Unknown preset "{preset}". Available presets: {presets}'
            )
            return

        flags = SEED_PRESETS[preset]
        seed = random.randint(0, 8999999999999999999)
        seed_str = f'Flags: {flags} Seed: {seed}'

        await self._roll_seed_raceinfo(seed_str)
        await self.send_message(f'{preset} - {seed_str}')
        await self.send_message('Seed rolling complete.  See race info for details.')
        self.seed_rolled = True
        self.logger.info('[%s] Seed rolled via !race %s: %s', self.data.get('name'), preset, seed_str)
```

**Step 4: Verify import still works**

Run: `python -c "from ttpbot.handler import TTPRaceHandler; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add ttpbot/handler.py && git commit -m "feat: add ex_flags and ex_race seed rolling commands"
```

---

## Task 4: Add TTP4 shortcuts and !help

**Files:**
- Modify: `ttpbot/handler.py`

Add these methods after `ex_race`.

**Step 1: Add TTP4 shortcut commands**

```python
    async def ex_ttp4(self, args, message):
        """!ttp4 -- Roll a random TTP Season 4 preset."""
        if self.sahasrahbot_present:
            return
        preset = random.choice(TTP4_PRESETS)
        await self.ex_race([preset], message)

    async def ex_ttp4rp(self, args, message):
        """!ttp4rp -- Roll the TTP4 Random% Remastered preset."""
        if self.sahasrahbot_present:
            return
        await self.ex_race(['ttp4rp'], message)

    async def ex_ttp4hopla(self, args, message):
        """!ttp4hopla -- Roll the TTP4 Hopla Remastered preset."""
        if self.sahasrahbot_present:
            return
        await self.ex_race(['ttp4hopla'], message)

    async def ex_ttp4consternation(self, args, message):
        """!ttp4consternation -- Roll the TTP4 Consternation Remastered preset."""
        if self.sahasrahbot_present:
            return
        await self.ex_race(['ttp4consternation'], message)
```

**Step 2: Add !help**

```python
    async def ex_help(self, args, message):
        """!help -- List available TTPBot commands."""
        lines = [
            'TTPBot commands:',
            '  Seed rolling (when SahasrahBot is offline):',
            '    !race <preset>              Roll a seed by preset name',
            '    !flags <flagstring>         Roll a seed with a custom flag string',
            '    !ttp4                       Random TTP Season 4 preset',
            '    !ttp4rp / !ttp4hopla / !ttp4consternation  TTP4 presets directly',
            '  Season info:',
            '    !schedule                   Today\'s remaining race times',
            '    !info                       Season info',
            '    !ttpflags                   TTP Season 4 flagset details',
        ]
        await self.send_message('\n'.join(lines))
```

**Step 3: Verify import**

Run: `python -c "from ttpbot.handler import TTPRaceHandler; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add ttpbot/handler.py && git commit -m "feat: add TTP4 shortcut commands and !help"
```

---

## Task 5: Restart service and verify

**Step 1: Restart TTPBot**

```powershell
powershell -Command "Start-Process powershell -ArgumentList '-Command','Restart-Service TTPBot' -Verb RunAs -Wait"
```

**Step 2: Verify service is running**

```powershell
powershell -Command "Get-Service TTPBot | Format-List Name, Status"
```

Expected: `Status: Running`

**Step 3: Tail the log and watch for startup**

```bash
tail -f D:/Projects/Streaming/TTPBot/ttpbot.log
```

Watch for: Bot connecting to racetime.gg, scheduler starting, no import errors.

**Step 4: In-room verification (tonight)**

In a live TTP race room, observe:
- With SahasrahBot online: type `!race ttp4rp` — TTPBot should NOT respond
- With SahasrahBot offline: type `!flags oIbnPfPb01Hll3D295IGDxxjR4UwfEok8P4MD` — TTPBot
  should respond with `Seed: {seed} - Flags: {flags}` followed by `Seed rolling complete.`
- `!help` should always respond regardless of SahasrahBot presence
- `!schedule` / `!info` / `!ttpflags` should always respond

**Step 5: Check SahasrahBot detection in logs**

If SahasrahBot IS online, the log should contain:
```
[z1r/roomname] SahasrahBot detected in chat history — seed commands deferred
```

If SahasrahBot is offline, no detection line; TTPBot handles seed commands.

---

## Notes for Future Work

- **TTP2 / TTP3 presets:** Add back to `SEED_PRESETS` once updated flagstrings are confirmed
  (Z1R client app was updated, old strings changed)
- **Expand to all Z1R races:** If SahasrahBot detection proves reliable tonight, assess removing
  the `should_handle()` goal filter in `bot.py` to cover all Z1R races (not just TTP Season 4)
- **`!race` preset list message:** The available-presets message can get long (27 items);
  consider trimming to just TTP4 presets in the error response if racers find it noisy
