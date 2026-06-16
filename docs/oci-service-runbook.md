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
sudo -u ttpbot /opt/ttpbot/.venv/bin/pip install -e /opt/ttpbot
sudo cp /opt/ttpbot/deploy/ttpbot.service /etc/systemd/system/ttpbot.service
sudo systemctl daemon-reload
```

## Configure Secrets

```bash
sudo install -m 600 -o root -g root /opt/ttpbot/deploy/ttpbot.env.example /etc/ttpbot.env
sudoedit /etc/ttpbot.env
```

Set the real racetime and Discord values. Do not store them in Git.

Validate that no required production value is blank without printing secrets:

```bash
sudo awk -F= '
  /^[A-Z0-9_]+=/{ if ($2 == "") { print "missing " $1; missing=1 } }
  END { exit missing ? 1 : 0 }
' /etc/ttpbot.env
```

## Migrate State From Windows

Stop the Windows service first, then copy:

```powershell
nssm stop TTPBot
scp -r created_races.json sent_webhooks.json learned_aliases.json chat_logs ubuntu@<coop-relay-host>:/tmp/ttpbot-state/
```

On the VM:

```bash
sudo install -d -o ttpbot -g ttpbot /var/lib/ttpbot
sudo cp /tmp/ttpbot-state/created_races.json /var/lib/ttpbot/ 2>/dev/null || true
sudo cp /tmp/ttpbot-state/sent_webhooks.json /var/lib/ttpbot/ 2>/dev/null || true
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
