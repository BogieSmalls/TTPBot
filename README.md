# TTPBot

<img src="ttpbot.png" alt="TTPBot logo" width="120" align="right" />

A [racetime.gg](https://racetime.gg) category bot for **Zelda 1 Randomizer Triforce Triple Play Season 4** — a 24-week competitive league (Feb 3 – Aug 8, 2026) running weekly race nights across 432 scheduled races on the [z1r](https://racetime.gg/z1r) category.

TTPBot handles everything around a TTP Season 4 race: opening the race room on schedule, announcing it in Discord, giving reminders, detecting the ROM hash, rolling seeds when SahasrahBot is offline, and archiving the room's chat log.

## What it does

### Scheduled race-room creation

TTPBot opens a fresh race room 30 minutes before every scheduled TTP Season 4 race. The schedule (US/Eastern) is:

| Day | Races |
|-----|-------|
| Mon–Fri | 8 PM, 10 PM, then 12 AM the following morning |
| Saturday | 12 PM, 3 PM, 6 PM |
| Sunday | No races |

Rooms use `streaming_required: true`, a 4-hour time limit, a 15-second start delay, and auto-start when all entrants ready up. Rooms are deduplicated across bot restarts via a persisted `created_races.json` so a service restart mid-slate won't double-open anything.

### Discord announcements

20 minutes before each race (10 minutes after the room opens), TTPBot posts a message to the configured Race Seekers Discord channel with an `@Race Seekers` mention and the room URL:

```
@Race Seekers Saturday TTP2: https://racetime.gg/z1r/...
```

The TTP number (1/2/3) is derived from the race's scheduled time.

### Per-race handler

Once a TTP room is live, TTPBot joins and handles:

- **Reminders** at T-10, T-5, T-1, and T-0 minutes.
- **Hash detection.** When entrants post the 4-item ROM hash in chat, TTPBot recognizes it — tolerating player aliases (`boomerang`, `tringle`, `ruppees`, …), multi-word forms (`spice rack`, `blue candle`), and typos. If 3 of 4 items match exactly, the 4th is fuzzy-matched and the new alias is auto-learned to `learned_aliases.json` for next time.
- **Chat logging.** Every message in the room is written to `chat_logs/<race-slug>.log`.
- **SahasrahBot stand-in seed rolling.** When SahasrahBot isn't in the room, TTPBot handles `!race <preset>`, `!flags <flagstring>`, and the season's curated pickers (`!ttp4`, `!ttp4rp`, `!ttp4hopla`, `!ttp4consternation`). Output format matches SahasrahBot exactly so it's a drop-in replacement. When SahasrahBot is present, TTPBot stays silent on seed commands and defers entirely — `!help` still works.

## Requirements

- Python ≥ 3.9 (tested on 3.13)
- [`racetime_bot`](https://github.com/racetimeGG/racetime-bot) ≥ 2.3
- A racetime.gg OAuth2 client (client_id + client_secret) with bot permissions on the target category
- A Discord webhook URL for race announcements

## Install

```
pip install -e .
```

## Run

```
python -m ttpbot <category_slug> <client_id> <client_secret>
```

Set the Discord webhook URL via environment variable:

```
# PowerShell
$env:TTPBOT_Z1R_WEBHOOK_URL = "https://discord.com/api/webhooks/..."

# bash
export TTPBOT_Z1R_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

If the env var is unset, the bot runs normally but skips Discord announcements (logged as a warning).

### Deployment (Windows service via NSSM)

```
nssm install TTPBot C:\Path\To\python.exe
nssm set TTPBot AppParameters -m ttpbot z1r <client_id> "<client_secret>"
nssm set TTPBot AppDirectory D:\Path\To\TTPBot
nssm set TTPBot AppStdout D:\Path\To\TTPBot\ttpbot.log
nssm set TTPBot AppStderr D:\Path\To\TTPBot\ttpbot.log
nssm set TTPBot AppEnvironmentExtra TTPBOT_Z1R_WEBHOOK_URL=https://discord.com/api/webhooks/...
nssm start TTPBot
```

## Configuration

All tunable values live in [`ttpbot/config.py`](ttpbot/config.py):

- `SEASON_START` / `SEASON_END` — season window
- `WEEKLY_SCHEDULE` — per-day race times
- `ROOM_OPEN_MINUTES_BEFORE` — how far ahead of race time to open the room (default 30)
- `WEBHOOK_MINUTES_BEFORE` — how far ahead to post the Discord announcement (default 20)
- `REMINDER_SCHEDULE` — which reminders to send and when
- `HASH_ALIASES` / `HASH_ALIASES_MULTI` — the canonical alias map for hash recognition
- `SEED_PRESETS` — 27 named Z1R presets (SahasrahBot parity)
- `TTP2_PRESETS` / `TTP3_PRESETS` / `TTP4_PRESETS` — pools for the `!ttpN` random pickers

## License

MIT
