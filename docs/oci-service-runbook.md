# TTPBot OCI Service Runbook

## Layout

- App: `/opt/ttpbot`
- Virtualenv: `/opt/ttpbot/.venv`
- State: `/var/lib/ttpbot`
- Secrets: `/etc/ttpbot.env`
- Service: `ttpbot.service`

## Install or Update Code

```bash
sudo useradd -r -s /usr/sbin/nologin ttpbot || true
sudo install -d -o ttpbot -g ttpbot /opt/ttpbot /var/lib/ttpbot

if [ ! -d /opt/ttpbot/.git ] && [ -n "$(find /opt/ttpbot -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "/opt/ttpbot exists but is not a Git checkout; inspect before continuing" >&2
  exit 1
fi

if [ ! -d /opt/ttpbot/.git ]; then
  sudo -u ttpbot git clone https://github.com/BogieSmalls/TTPBot.git /opt/ttpbot
else
  sudo -u ttpbot git -C /opt/ttpbot pull --ff-only
fi

sudo -u ttpbot python3 -m venv /opt/ttpbot/.venv
sudo -u ttpbot /opt/ttpbot/.venv/bin/pip install -U pip
sudo -u ttpbot /opt/ttpbot/.venv/bin/pip install -r /opt/ttpbot/requirements.lock
sudo -u ttpbot /opt/ttpbot/.venv/bin/pip install --no-deps -e /opt/ttpbot
sudo cp /opt/ttpbot/deploy/ttpbot.service /etc/systemd/system/ttpbot.service
sudo systemctl daemon-reload
```

## Configure Secrets

```bash
sudo install -m 0640 -o root -g ttpbot /opt/ttpbot/deploy/ttpbot.env.example /etc/ttpbot.env
sudoedit /etc/ttpbot.env
```

Set the real Racetime and optional paired Discord values. `/etc/ttpbot.env` is
owned `root:ttpbot` with mode `0640`. Never print or store credential values,
webhook URLs, role IDs, or tokens in logs/evidence/Git.

Choose exactly one origin. The category slug and the season goal differ per
origin, so they must be set together:

```text
# racetime.gg (current) - goal "TTP Season 5"
TTPBOT_RACETIME_ORIGIN=https://racetime.gg
TTPBOT_CATEGORY_SLUG=z1r

# Z1RR Raceroom (use instead, never alongside it) - goal "TTP: Season 5"
TTPBOT_RACETIME_ORIGIN=https://raceroom.z1rracing.com
TTPBOT_CATEGORY_SLUG=z1rr
```

`racetime.gg/z1rr` does not exist and `raceroom.z1rracing.com/z1r` does not
exist, so a mismatched pair fails closed. The OAuth client pair is per-origin
too: a raceroom client id/secret returns `401 invalid_client` on racetime.gg.
Confirm the pair before restarting:

```bash
curl -s -o /dev/null -w '%{http_code}
' -X POST "$TTPBOT_RACETIME_ORIGIN/o/token"   -d grant_type=client_credentials -d client_id=... -d client_secret=...
```

Expect `200`. `GOAL_NAME` in `ttpbot/config.py` must match the origin's goal
string exactly; verify against `<origin>/<slug>/data`.

Validate locally and then probe read-only. `ttpbot-preflight` sources `/etc/ttpbot.env`; it does not create a room or send Discord:

```bash
sudo -u ttpbot /usr/local/bin/ttpbot-preflight
```

## Switching Destination (racetime.gg <-> Raceroom)

Each destination keeps its own complete env file, so neither credential pair can
overwrite the other. `/etc/ttpbot.env` is a copy of whichever is active:

```text
/etc/ttpbot.env.racetimegg   # origin https://racetime.gg, slug z1r
/etc/ttpbot.env.raceroom     # origin https://raceroom.z1rracing.com, slug z1rr
/etc/ttpbot.env              # copy of the active one
```

All three are `root:ttpbot` mode `0640`. To switch:

```bash
sudo systemctl stop ttpbot
sudo cp /etc/ttpbot.env.racetimegg /etc/ttpbot.env   # or .raceroom
sudo chown root:ttpbot /etc/ttpbot.env && sudo chmod 0640 /etc/ttpbot.env
```

Scheduler state is destination-bound and fails closed: `created_races.json` and
`sent_webhooks.json` carry a `destination_key`, and loading them under a
different origin/category raises `state belongs to another destination`
(preflight reports `"state": false`). Park the outgoing destination's files
rather than deleting or editing them; a missing file loads as empty, which is
correct for a destination that has opened no rooms yet:

```bash
sudo install -d -o ttpbot -g ttpbot -m 0750 /var/lib/ttpbot/<outgoing>-state
sudo mv /var/lib/ttpbot/created_races.json /var/lib/ttpbot/sent_webhooks.json   /var/lib/ttpbot/<outgoing>-state/
sudo chown -R ttpbot:ttpbot /var/lib/ttpbot/<outgoing>-state
```

`learned_aliases.json` and `chat_logs/` are not destination-bound; leave them.
Re-run `ttpbot-preflight` and require `"ok":true` before starting.

## Explicit legacy state migration

Do not copy a legacy document over a v2 destination-bound file. Stop the old
scheduler first. Verify the process and scheduler lock are absent. Copy legacy
files to a separate staging directory:

```powershell
nssm stop TTPBot
scp -r created_races.json sent_webhooks.json learned_aliases.json chat_logs ubuntu@<coop-relay-host>:/tmp/ttpbot-state/
```

Assert the exact legacy origin/category when migrating. The command creates a
timestamped read-only backup and will not guess a destination:

```bash
sudo install -d -o ttpbot -g ttpbot /var/lib/ttpbot
sudo -u ttpbot /opt/ttpbot/.venv/bin/python -m ttpbot.migrate_state \
  --legacy-dir /tmp/ttpbot-state --origin https://racetime.gg --category z1rr
sudo cp /tmp/ttpbot-state/learned_aliases.json /var/lib/ttpbot/ 2>/dev/null || true
sudo cp -r /tmp/ttpbot-state/chat_logs /var/lib/ttpbot/ 2>/dev/null || true
sudo chown -R ttpbot:ttpbot /var/lib/ttpbot
```

## Start and Verify

```bash
sudo systemctl enable --now ttpbot
sudo systemctl status ttpbot
sudo journalctl -u ttpbot -f
```

Verify:

- The service is active.
- Logs show racetime connection without repeated exceptions.
- State files update under `/var/lib/ttpbot`.
- The next scheduled room and Discord webhook happen once.

## Retire Windows Service

Only after cloud verification:

```powershell
nssm stop TTPBot
nssm set TTPBot Start SERVICE_DISABLED
```

## Rollback

Stop the cloud service before restarting Windows:

```bash
sudo systemctl stop ttpbot
```

If cloud already created or announced a room, copy the updated JSON state files
from `/var/lib/ttpbot` back to the Windows TTPBot directory before restarting
NSSM.

## Provider cutover

This is an operations change, never a G0 test. Execute outside every room-open
window: `ROOM_OPEN_MINUTES_BEFORE + 10` is the minimum blackout buffer.

1. Stop and disable the old scheduler. Verify the process and scheduler lock are
   absent; never run both schedulers.
2. Back up the prior release identity, `/etc/ttpbot.env`, `created_races.json`,
   `sent_webhooks.json`, chat state, and service status without displaying secrets.
3. Install the tested commit and exact `requirements.lock`.
4. Migrate or archive legacy state with the asserted old `destination_key`; do
   not delete the old environment or state. Initialize fresh v2 state for a new
   destination rather than relabeling it.
5. Write the new origin/category/credentials to a temporary root-only file, set
   `root:ttpbot 0640`, and atomically replace `/etc/ttpbot.env`.
6. Run `--check-config`, then `--probe`. Hold if the destination, OAuth/category,
   clock, state, collision, announcement, or lock check fails.
7. Enable/start one service and prove exactly one scheduler lock exists.
8. Observe the next room: one provider/category POST, canonical room URL, one
   `created_races.json` entry, one Discord announcement, and one
   `sent_webhooks.json` entry after restart.
9. Retain the old release/env/state encrypted for rollback; do not delete them.

## Provider rollback

1. Stop the new scheduler first and verify its process and lock are absent.
2. Restore the prior release, environment, and state as one set.
3. Probe the old destination with `--check-config` and `--probe` while stopped.
4. Query both providers for the upcoming slot. If a new-destination room exists,
   do not create a counterpart room; hold the scheduler and communicate/cancel
   manually.
5. Start the old service only after the collision check passes. Never run both
   schedulers.

## Acceptance and secret handling

Record only commit/lock hash, safe `destination_key`, preflight booleans, service
and lock status, room URL, one Discord announcement result, v2 state hashes, and
rollback outcome, and never print OAuth client secrets, access tokens, webhook URLs,
role IDs, environment contents, provider response bodies, or state contents.
