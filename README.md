# TTPBot

<img src="ttpbot.png" alt="TTPBot logo" width="120" align="right" />

A provider-neutral category bot for **Zelda 1 Randomizer Triforce Triple Play**. The same release can target `https://racetime.gg/z1rr` or `https://raceroom.z1rracing.com/z1rr`; its destination is selected only by validated runtime configuration.

TTPBot handles everything around a scheduled Triforce Triple Play race: opening the race room on schedule, announcing it in Discord, giving reminders, detecting the ROM hash, rolling seeds when SahasrahBot is offline, and archiving the room's chat log.

## What it does

### Scheduled race-room creation

TTPBot opens a fresh race room 30 minutes before every scheduled Triforce Triple Play race. The normal schedule (US/Eastern) is:

| Day | Races |
|-----|-------|
| Mon–Fri | 8 PM, 10 PM, then 12 AM the following morning |
| Saturday | 12 PM, 3 PM, 6 PM |
| Sunday | No races |

Regular-season rooms use the `TTP Season 4` goal through July 4, 2026. Later normal scheduled rooms use the `Beat the game` goal and an `info_bot` label beginning with `Triforce Triple Play | Scheduled:`. Rooms use `streaming_required: true`, a 4-hour time limit, a 15-second start delay, and auto-start when all entrants ready up. Rooms are deduplicated across bot restarts via a persisted `created_races.json` so a service restart mid-slate won't double-open anything.

### Discord announcements

20 minutes before each race (10 minutes after the room opens), TTPBot posts a message to the configured Race Seekers Discord channel with an `@Race Seekers` mention and the room URL:

```
@Race Seekers Saturday TTP2: https://racetime.gg/z1rr/...
```

The TTP number (1/2/3) is derived from the race's scheduled time.

### Per-race handler

Once a TTP room is live, TTPBot joins and handles:

- **Reminders** at T-10, T-5, T-1, and T-0 minutes.
- **Hash detection.** When entrants post the 4-item ROM hash in chat, TTPBot recognizes it — tolerating player aliases (`boomerang`, `tringle`, `ruppees`, …), multi-word forms (`spice rack`, `blue candle`), and typos. If 3 of 4 items match exactly, the 4th is fuzzy-matched and the new alias is auto-learned to `learned_aliases.json` for next time.
- **Chat logging.** Every message in the room is written to `chat_logs/<race-slug>.log`.
- **SahasrahBot stand-in seed rolling.** When SahasrahBot isn't in the room, TTPBot handles `!race <preset>`, `!flags <flagstring>`, and the season's curated pickers (`!ttp4`, `!ttp4rp`, `!ttp4hopla`, `!ttp4consternation`). Output format matches SahasrahBot exactly so it's a drop-in replacement. When SahasrahBot is present, TTPBot stays silent on seed commands and defers entirely — `!help` still works.

## Requirements

- Python ≥ 3.10 (tested on 3.13)
- The exact packages in `requirements.lock`
- An OAuth2 client with bot permissions on the configured Racetime category
- Optional paired Discord webhook and Race Seekers role configuration

## Install

```
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

## Provider configuration

Approved Racetime.gg category:

```text
TTPBOT_RACETIME_ORIGIN=https://racetime.gg
TTPBOT_CATEGORY_SLUG=z1rr
```

Self-hosted contingency:

```text
TTPBOT_RACETIME_ORIGIN=https://raceroom.z1rracing.com
TTPBOT_CATEGORY_SLUG=z1rr
```

The destination-bound `created_races.json` and `sent_webhooks.json` store the
canonical `destination_key`; a state file from another origin/category fails
closed. Set every variable shown in `deploy/ttpbot.env.example`. Webhook and role
must be both set or both empty.

Validate without creating a room or sending a webhook:

```bash
python -m ttpbot --check-config
python -m ttpbot --probe
```

The probe uses only OAuth token and category GET calls. Safe output contains no
credential, webhook, token, role ID, response body, or filesystem path.

## Run

```bash
python -m ttpbot
```

The service host enforces exactly one scheduler with a nonblocking `flock`.
Never run a manual scheduler beside the service.

### Deployment (Linux systemd)

Production runs as a standalone `ttpbot.service` on the OCI `coop-relay` VM.
The Linux service reads credentials from `/etc/ttpbot.env` and writes runtime
state to `/var/lib/ttpbot` via `TTPBOT_DATA_DIR`.

See [docs/oci-service-runbook.md](docs/oci-service-runbook.md).

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
