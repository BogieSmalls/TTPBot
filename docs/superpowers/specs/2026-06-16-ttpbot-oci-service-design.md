# TTPBot OCI Service Migration Design

**Date:** 2026-06-16
**Status:** Approved approach, pending implementation plan

## Context

TTPBot currently runs on the Windows streaming machine as an NSSM-managed Python
service. Oracle Cloud already hosts the lightweight `z1rr-coop` relay instance,
and TTPBot is small enough to co-locate there without sharing process state or
deployment ownership with the relay.

The migration target is a standalone Linux service on the existing
`coop-relay` OCI VM. After the cloud service is verified, the Windows NSSM
service will be stopped and disabled.

## Goals

- Run TTPBot as the only active production bot from Oracle Cloud.
- Keep TTPBot isolated from the coop relay despite sharing the same VM.
- Move production runtime state off the Windows machine:
  `created_races.json`, `sent_webhooks.json`, `learned_aliases.json`, and
  `chat_logs/`.
- Move racetime and Discord credentials into a Linux-only secret file.
- Make deploy/update/restart operations repeatable enough to use during the
  2026 TTP Season 4 schedule.
- Provide a rollback path that avoids duplicate race room creation.

## Non-Goals

- Containerizing TTPBot or the relay.
- Folding TTPBot into the `z1rr-coop` repository.
- Changing TTPBot's race behavior, schedule, reminders, seed commands, or
  webhook message content.
- Opening any inbound firewall ports for TTPBot. The bot only needs outbound
  HTTPS/WSS access to racetime.gg and outbound HTTPS to Discord.

## Architecture

The `coop-relay` VM will run two independent services:

| Service | User | App directory | State directory | Logs |
|---|---|---|---|---|
| coop relay | `relay` | `/opt/z1rr-coop-relay` | none | journald |
| TTPBot | `ttpbot` | `/opt/ttpbot` | `/var/lib/ttpbot` | journald plus chat logs |

TTPBot remains deployed from the standalone Git repository:
`https://github.com/BogieSmalls/TTPBot.git`.

The service will use a dedicated virtual environment at `/opt/ttpbot/.venv`.
The systemd unit will run `python -m ttpbot` from that virtual environment.
The unit will not share the relay's user, virtualenv, app directory, or
environment.

## Configuration and Secrets

Production secrets will live in `/etc/ttpbot.env`, readable only by root and
the service manager. The file will define:

```bash
TTPBOT_CATEGORY_SLUG=z1r
TTPBOT_RACETIME_CLIENT_ID=...
TTPBOT_RACETIME_CLIENT_SECRET=...
TTPBOT_Z1R_WEBHOOK_URL=...
TTPBOT_DATA_DIR=/var/lib/ttpbot
```

TTPBot's existing command-line credentials are acceptable for local and
backward-compatible usage, but the Linux service should prefer environment
variables so the racetime client secret does not appear in normal process
listings.

For the production service, all five variables above are required. The app
should fail fast when racetime credentials are missing. The deployment runbook
should also validate that the Discord webhook is present before enabling the
service, even if local development keeps the current "warn and skip webhook"
behavior.

## Runtime State

The current code stores writable files relative to the source checkout. For
cloud deployment, TTPBot should support a configurable data directory:
`TTPBOT_DATA_DIR`.

When `TTPBOT_DATA_DIR` is set:

- `created_races.json` writes to `/var/lib/ttpbot/created_races.json`
- `sent_webhooks.json` writes to `/var/lib/ttpbot/sent_webhooks.json`
- `learned_aliases.json` writes to `/var/lib/ttpbot/learned_aliases.json`
- chat logs write to `/var/lib/ttpbot/chat_logs/`

When the variable is unset, local behavior stays compatible with today's repo
root files.

This keeps code updates under `/opt/ttpbot` from mixing with production state
and makes rollback/copy operations explicit.

## Systemd Service

The service should be named `ttpbot.service`.

Expected unit behavior:

- `User=ttpbot` and `Group=ttpbot`
- `WorkingDirectory=/opt/ttpbot`
- `EnvironmentFile=/etc/ttpbot.env`
- `Environment=PYTHONDONTWRITEBYTECODE=1`
- `ExecStart=/opt/ttpbot/.venv/bin/python -m ttpbot`
- `Restart=on-failure`
- `RestartSec=5`
- `StateDirectory=ttpbot`
- `NoNewPrivileges=true`
- Restrict writes to the state directory, while leaving network access
  available.

stdout/stderr will go to journald. Per-race chat logs remain normal files under
the state directory because they are race artifacts, not service diagnostics.

## Deployment Flow

1. On the VM, create a dedicated `ttpbot` system user.
2. Clone or update the TTPBot repo into `/opt/ttpbot`.
3. Create `/opt/ttpbot/.venv` and install the package in editable mode.
4. Create `/etc/ttpbot.env` from the existing private credential values.
5. Create or verify `/var/lib/ttpbot` ownership for the `ttpbot` user.
6. Install `ttpbot.service`.
7. Migrate state from Windows to `/var/lib/ttpbot`.
8. Start the cloud service only when the Windows NSSM service is stopped.

The deploy path should be documented in TTPBot's README or a dedicated
deployment runbook so future updates are:

```bash
sudo -u ttpbot git -C /opt/ttpbot pull
sudo -u ttpbot /opt/ttpbot/.venv/bin/pip install -e /opt/ttpbot
sudo systemctl restart ttpbot
```

## Cutover Plan

The cutover must avoid overlapping active schedulers. TTPBot has production
side effects: it creates racetime rooms, sends Discord webhooks, welcomes rooms,
and responds to commands.

Preferred cutover window:

- Stop the Windows NSSM service outside a room-open window, ideally when no TTP
  room is actively preparing or racing.
- Copy the latest state files and chat logs from Windows to the VM.
- Start `ttpbot.service` on the VM.
- Watch journald for startup and racetime connection errors.
- Observe the next scheduled room creation and webhook from the cloud service.
- After successful verification, disable the Windows NSSM service.

The Windows service should not be disabled until cloud verification is complete.

## Verification

Local verification before deploy:

- CLI still accepts the existing positional arguments.
- CLI also starts from environment variables without credentials in argv.
- `TTPBOT_DATA_DIR` redirects state and chat log paths.
- Existing hash parsing and command behavior are unchanged.

Cloud verification after deploy:

- `systemctl status ttpbot` is active.
- `journalctl -u ttpbot` shows no repeated exceptions.
- State files are created or updated in `/var/lib/ttpbot`.
- The bot creates the next scheduled TTP room only once.
- The Discord Race Seekers webhook posts once for that room.
- In-room behavior works: welcome message, reminders, hash confirmation, and
  seed-command deferral when SahasrahBot is present.

## Rollback

Rollback means exactly one service is active at a time.

If the cloud service fails before it creates rooms or sends webhooks:

1. Stop `ttpbot.service`.
2. Start the Windows NSSM service.

If the cloud service has already created rooms or sent webhooks:

1. Stop `ttpbot.service`.
2. Copy `/var/lib/ttpbot/created_races.json` and
   `/var/lib/ttpbot/sent_webhooks.json` back to the Windows TTPBot directory.
3. Copy `learned_aliases.json` back if it changed.
4. Start the Windows NSSM service.

This preserves dedupe state and prevents the Windows service from reopening or
re-announcing already handled races.

## Risks

- Running both services at once can double-create rooms or double-send webhooks.
  Cutover must explicitly stop one before starting the other.
- A missing or malformed `/etc/ttpbot.env` can start the service without
  Discord announcements or without racetime credentials. Startup validation
  should fail fast for required racetime values, and the deployment runbook
  should fail preflight if the production webhook is absent.
- If state files stay source-relative, deploy updates can overwrite or lose
  runtime state. `TTPBOT_DATA_DIR` removes that risk.
- Passing secrets as process args exposes them to local process inspection.
  Environment-file startup avoids this for the Linux service.

## Open Questions

- Whether to keep a short retained backup of `/var/lib/ttpbot` before each
  deploy. This is useful but not required for the first migration.
- Whether to add log rotation for chat logs after the season. The existing log
  volume is small enough for the first cloud cutover.
