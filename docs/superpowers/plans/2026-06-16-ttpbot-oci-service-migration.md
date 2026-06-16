# TTPBot OCI Service Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move TTPBot from the Windows NSSM service to a standalone systemd service on the existing Oracle Cloud `coop-relay` VM.

**Architecture:** Keep TTPBot as its own Python package and service. Add a runtime data-directory abstraction so writable state lives outside the source checkout on Linux, add environment-based startup so secrets are not process arguments, and document a one-active-service cutover from Windows to OCI.

**Tech Stack:** Python 3.9+, stdlib `unittest`, `racetime_bot`, Linux systemd, OCI Ubuntu VM, SSH/SCP.

---

## File Structure

- Create `ttpbot/paths.py`
  - Owns the runtime data directory decision.
  - Defaults to the current repo root for local Windows compatibility.
  - Uses `TTPBOT_DATA_DIR` when set.

- Create `ttpbot/runtime_config.py`
  - Resolves category/client credentials from CLI args or environment variables.
  - Provides small pure functions that can be tested without starting the bot.

- Modify `ttpbot/__init__.py`
  - Make the three positional args optional.
  - Resolve missing values from environment variables.
  - Fail fast with clear missing-variable names when required config is absent.

- Modify `ttpbot/bot.py`
  - Replace repo-root state file constants with `runtime_path(...)`.
  - Ensure parent directories exist before writing JSON state.

- Modify `ttpbot/handler.py`
  - Replace repo-root `chat_logs` and `learned_aliases.json` paths with `runtime_path(...)`.
  - Ensure parent directories exist before writing aliases or chat logs.

- Create `tests/test_paths.py`
  - Tests default local data directory and `TTPBOT_DATA_DIR` override.

- Create `tests/test_runtime_config.py`
  - Tests CLI argument precedence, environment fallback, and missing required values.

- Create `tests/test_runtime_state_paths.py`
  - Tests `bot.py` and `handler.py` use the configured data directory after module import.

- Create `deploy/ttpbot.service`
  - Production systemd service unit for `/opt/ttpbot`.

- Create `deploy/ttpbot.env.example`
  - Non-secret template for `/etc/ttpbot.env`.

- Create `docs/oci-service-runbook.md`
  - Operator-facing install, update, cutover, verification, and rollback runbook.

- Modify `README.md`
  - Keep the existing local/NSSM instructions but point Linux production users to the OCI runbook.

## Task 0: Local Test Environment Prep

**Files:**
- No source files.

- [ ] **Step 1: Verify Python version**

Run:

```powershell
python -c "import sys; assert sys.version_info >= (3, 9), sys.version"
```

Expected: exits 0.

- [ ] **Step 2: Install the package in editable mode**

Run:

```powershell
python -m pip install -e .
```

Expected: package installs with `racetime_bot` dependency available. This prevents
early unit tests from failing on the package-level `ttpbot.__init__` import before
the new modules exist.

- [ ] **Step 3: Smoke-check current package import**

Run:

```powershell
python -c "import racetime_bot; import ttpbot; print('ok')"
```

Expected: prints `ok`.

## Task 1: Runtime Data Directory Helper

**Files:**
- Create: `ttpbot/paths.py`
- Create: `tests/test_paths.py`

- [ ] **Step 1: Write failing tests for path resolution**

Create `tests/test_paths.py`:

```python
import tempfile
import unittest
from pathlib import Path

from ttpbot.paths import PROJECT_ROOT, data_dir, ensure_parent_dir, runtime_path


class RuntimePathTests(unittest.TestCase):
    def test_data_dir_defaults_to_project_root(self):
        env = {}

        self.assertEqual(data_dir(env), PROJECT_ROOT)
        self.assertEqual(runtime_path('created_races.json', env), PROJECT_ROOT / 'created_races.json')

    def test_data_dir_uses_ttpbot_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {'TTPBOT_DATA_DIR': tmp}

            self.assertEqual(data_dir(env), Path(tmp))
            self.assertEqual(runtime_path('chat_logs', env), Path(tmp) / 'chat_logs')

    def test_blank_ttpbot_data_dir_uses_project_root(self):
        env = {'TTPBOT_DATA_DIR': '   '}

        self.assertEqual(data_dir(env), PROJECT_ROOT)

    def test_ensure_parent_dir_creates_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nested' / 'state.json'

            ensure_parent_dir(path)

            self.assertTrue(path.parent.is_dir())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m unittest tests.test_paths -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ttpbot.paths'`.

- [ ] **Step 3: Implement the helper**

Create `ttpbot/paths.py`:

```python
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_dir(env=None):
    source = os.environ if env is None else env
    configured = str(source.get('TTPBOT_DATA_DIR') or '').strip()
    if configured:
        return Path(configured)
    return PROJECT_ROOT


def runtime_path(name, env=None):
    return data_dir(env) / name


def ensure_parent_dir(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run the path tests**

Run:

```powershell
python -m unittest tests.test_paths -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ttpbot/paths.py tests/test_paths.py
git commit -m "feat(runtime): add configurable data directory"
```

## Task 2: Environment-Based Startup Config

**Files:**
- Create: `ttpbot/runtime_config.py`
- Create: `tests/test_runtime_config.py`
- Modify: `ttpbot/__init__.py`

- [ ] **Step 1: Write failing runtime config tests**

Create `tests/test_runtime_config.py`:

```python
import argparse
import unittest

from ttpbot.runtime_config import missing_config_names, resolve_bot_config


def args(category_slug=None, client_id=None, client_secret=None):
    return argparse.Namespace(
        category_slug=category_slug,
        client_id=client_id,
        client_secret=client_secret,
    )


class RuntimeConfigTests(unittest.TestCase):
    def test_cli_values_win_over_environment(self):
        config = resolve_bot_config(
            args('z1r', 'cli-id', 'cli-secret'),
            {
                'TTPBOT_CATEGORY_SLUG': 'env-category',
                'TTPBOT_RACETIME_CLIENT_ID': 'env-id',
                'TTPBOT_RACETIME_CLIENT_SECRET': 'env-secret',
            },
        )

        self.assertEqual(config.category_slug, 'z1r')
        self.assertEqual(config.client_id, 'cli-id')
        self.assertEqual(config.client_secret, 'cli-secret')

    def test_environment_supplies_missing_values(self):
        config = resolve_bot_config(
            args(),
            {
                'TTPBOT_CATEGORY_SLUG': 'z1r',
                'TTPBOT_RACETIME_CLIENT_ID': 'env-id',
                'TTPBOT_RACETIME_CLIENT_SECRET': 'env-secret',
            },
        )

        self.assertEqual(config.category_slug, 'z1r')
        self.assertEqual(config.client_id, 'env-id')
        self.assertEqual(config.client_secret, 'env-secret')

    def test_missing_config_names_uses_env_var_names(self):
        config = resolve_bot_config(
            args(category_slug='z1r'),
            {'TTPBOT_RACETIME_CLIENT_ID': 'env-id'},
        )

        self.assertEqual(
            missing_config_names(config),
            ['TTPBOT_RACETIME_CLIENT_SECRET'],
        )

    def test_blank_values_are_missing(self):
        config = resolve_bot_config(
            args(category_slug='  ', client_id='', client_secret=None),
            {
                'TTPBOT_CATEGORY_SLUG': ' ',
                'TTPBOT_RACETIME_CLIENT_ID': '',
                'TTPBOT_RACETIME_CLIENT_SECRET': '',
            },
        )

        self.assertEqual(
            missing_config_names(config),
            [
                'TTPBOT_CATEGORY_SLUG',
                'TTPBOT_RACETIME_CLIENT_ID',
                'TTPBOT_RACETIME_CLIENT_SECRET',
            ],
        )


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m unittest tests.test_runtime_config -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ttpbot.runtime_config'`.

- [ ] **Step 3: Implement runtime config helper**

Create `ttpbot/runtime_config.py`:

```python
from dataclasses import dataclass
import os
from typing import Optional


@dataclass(frozen=True)
class BotRuntimeConfig:
    category_slug: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]


def _clean(value):
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _arg_or_env(args, attr, env, env_name):
    return _clean(getattr(args, attr, None)) or _clean(env.get(env_name))


def resolve_bot_config(args, env=None):
    source = os.environ if env is None else env
    return BotRuntimeConfig(
        category_slug=_arg_or_env(args, 'category_slug', source, 'TTPBOT_CATEGORY_SLUG'),
        client_id=_arg_or_env(args, 'client_id', source, 'TTPBOT_RACETIME_CLIENT_ID'),
        client_secret=_arg_or_env(args, 'client_secret', source, 'TTPBOT_RACETIME_CLIENT_SECRET'),
    )


def missing_config_names(config):
    missing = []
    if not config.category_slug:
        missing.append('TTPBOT_CATEGORY_SLUG')
    if not config.client_id:
        missing.append('TTPBOT_RACETIME_CLIENT_ID')
    if not config.client_secret:
        missing.append('TTPBOT_RACETIME_CLIENT_SECRET')
    return missing
```

- [ ] **Step 4: Run runtime config tests**

Run:

```powershell
python -m unittest tests.test_runtime_config -v
```

Expected: PASS.

- [ ] **Step 5: Update CLI startup**

Modify `ttpbot/__init__.py`:

```python
import argparse
import logging
import sys

from .bot import TTPBot
from .runtime_config import missing_config_names, resolve_bot_config


def main():
    parser = argparse.ArgumentParser(
        description='TTP Season 4 Bot for racetime.gg Z1R races',
    )
    parser.add_argument('category_slug', nargs='?',
                        help='racetime.gg category slug (or TTPBOT_CATEGORY_SLUG)')
    parser.add_argument('client_id', nargs='?',
                        help='racetime.gg OAuth2 client ID (or TTPBOT_RACETIME_CLIENT_ID)')
    parser.add_argument('client_secret', nargs='?',
                        help='racetime.gg OAuth2 client secret (or TTPBOT_RACETIME_CLIENT_SECRET)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--host', type=str, default=None,
                        help='Override racetime.gg hostname (debug only)')
    parser.add_argument('--insecure', action='store_true',
                        help='Use HTTP/WS instead of HTTPS/WSS (debug only)')

    args = parser.parse_args()
    config = resolve_bot_config(args)
    missing = missing_config_names(config)
    if missing:
        parser.error('missing required config: ' + ', '.join(missing))

    logger = logging.getLogger('ttpbot')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(name)s (%(levelname)s) :: %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    if args.host:
        TTPBot.racetime_host = args.host
    if args.insecure:
        TTPBot.racetime_secure = False

    bot = TTPBot(
        category_slug=config.category_slug,
        client_id=config.client_id,
        client_secret=config.client_secret,
        logger=logger,
    )
    bot.run()


if __name__ == '__main__':
    main()
```

- [ ] **Step 6: Run config tests again**

Run:

```powershell
python -m unittest tests.test_runtime_config -v
```

Expected: PASS.

- [ ] **Step 7: Smoke-check CLI help**

Run:

```powershell
python -m ttpbot --help
```

Expected: exits 0 and shows positional args as optional.

- [ ] **Step 8: Smoke-check missing config failure**

Run:

```powershell
python -m ttpbot
```

Expected: exits non-zero with a message listing `TTPBOT_CATEGORY_SLUG`, `TTPBOT_RACETIME_CLIENT_ID`, and `TTPBOT_RACETIME_CLIENT_SECRET`.

- [ ] **Step 9: Commit**

```bash
git add ttpbot/runtime_config.py ttpbot/__init__.py tests/test_runtime_config.py
git commit -m "feat(runtime): allow environment-based startup"
```

## Task 3: Move Writable State to Runtime Data Directory

**Files:**
- Modify: `ttpbot/bot.py`
- Modify: `ttpbot/handler.py`
- Create: `tests/test_runtime_state_paths.py`

- [ ] **Step 1: Write failing state-path tests**

Create `tests/test_runtime_state_paths.py`:

```python
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class RuntimeStatePathTests(unittest.TestCase):
    def test_bot_state_files_use_ttpbot_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': tmp}):
                import ttpbot.bot as bot_module
                importlib.reload(bot_module)

                self.assertEqual(Path(bot_module.CREATED_RACES_FILE), Path(tmp) / 'created_races.json')
                self.assertEqual(Path(bot_module.SENT_WEBHOOKS_FILE), Path(tmp) / 'sent_webhooks.json')

    def test_handler_state_files_use_ttpbot_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': tmp}):
                import ttpbot.handler as handler_module
                importlib.reload(handler_module)

                self.assertEqual(Path(handler_module.CHAT_LOG_DIR), Path(tmp) / 'chat_logs')
                self.assertEqual(Path(handler_module.LEARNED_ALIASES_FILE), Path(tmp) / 'learned_aliases.json')

    def test_save_learned_alias_creates_data_dir_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / 'state'
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': str(nested)}):
                import ttpbot.handler as handler_module
                importlib.reload(handler_module)

                handler_module._save_learned_alias('trianlge', 'Triforce')

                self.assertTrue((nested / 'learned_aliases.json').is_file())

    def test_save_created_races_creates_data_dir_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / 'state'
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': str(nested)}):
                import ttpbot.bot as bot_module
                importlib.reload(bot_module)

                bot = object.__new__(bot_module.TTPBot)
                bot.created_races = {'2026-06-16T20:00:00-04:00': 'https://racetime.gg/z1r/test'}
                bot.logger = Mock()

                bot._save_created_races()

                self.assertTrue((nested / 'created_races.json').is_file())

    def test_save_sent_webhooks_creates_data_dir_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / 'state'
            with patch.dict(os.environ, {'TTPBOT_DATA_DIR': str(nested)}):
                import ttpbot.bot as bot_module
                importlib.reload(bot_module)

                bot = object.__new__(bot_module.TTPBot)
                bot.sent_webhooks = {'2026-06-16T20:00:00-04:00'}
                bot.logger = Mock()

                bot._save_sent_webhooks()

                self.assertTrue((nested / 'sent_webhooks.json').is_file())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m unittest tests.test_runtime_state_paths -v
```

Expected: FAIL because the modules still use repo-root paths.

- [ ] **Step 3: Refactor `bot.py` state paths**

Modify imports in `ttpbot/bot.py`:

```python
from .paths import ensure_parent_dir, runtime_path
```

Replace the existing constants with:

```python
CREATED_RACES_FILE = runtime_path('created_races.json')
SENT_WEBHOOKS_FILE = runtime_path('sent_webhooks.json')
```

Before writing each JSON file, call `ensure_parent_dir(...)`:

```python
    def _save_created_races(self):
        """Persist created_races dict to disk."""
        try:
            ensure_parent_dir(CREATED_RACES_FILE)
            with open(CREATED_RACES_FILE, 'w') as f:
                json.dump(self.created_races, f)
        except Exception:
            self.logger.error('Error saving created_races', exc_info=True)
```

Apply the same parent-directory call in `_save_sent_webhooks`.

- [ ] **Step 4: Refactor `handler.py` state paths**

Modify imports in `ttpbot/handler.py`:

```python
from .paths import ensure_parent_dir, runtime_path
```

Replace:

```python
CHAT_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'chat_logs')
LEARNED_ALIASES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'learned_aliases.json'
)
```

with:

```python
CHAT_LOG_DIR = runtime_path('chat_logs')
LEARNED_ALIASES_FILE = runtime_path('learned_aliases.json')
```

In `_save_learned_alias`, before opening the file for write, add:

```python
        ensure_parent_dir(LEARNED_ALIASES_FILE)
```

In `_log_chat`, the existing `os.makedirs(CHAT_LOG_DIR, exist_ok=True)` can stay. `Path` values are accepted by `os.makedirs` and `os.path.join`.

- [ ] **Step 5: Run state-path tests**

Run:

```powershell
python -m unittest tests.test_runtime_state_paths -v
```

Expected: PASS.

- [ ] **Step 6: Run all local unit tests**

Run:

```powershell
python -m unittest discover -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add ttpbot/bot.py ttpbot/handler.py tests/test_runtime_state_paths.py
git commit -m "feat(runtime): store bot state in data directory"
```

## Task 4: Linux Service Assets

**Files:**
- Create: `deploy/ttpbot.service`
- Create: `deploy/ttpbot.env.example`

- [ ] **Step 1: Create systemd service unit**

Create `deploy/ttpbot.service`:

```ini
[Unit]
Description=TTPBot racetime.gg bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ttpbot
Group=ttpbot
WorkingDirectory=/opt/ttpbot
EnvironmentFile=/etc/ttpbot.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/ttpbot/.venv/bin/python -m ttpbot
Restart=on-failure
RestartSec=5
StateDirectory=ttpbot
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/ttpbot

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create env template**

Create `deploy/ttpbot.env.example`:

```bash
# Copy to /etc/ttpbot.env on the VM. Do not commit real values.
TTPBOT_CATEGORY_SLUG=z1r
TTPBOT_RACETIME_CLIENT_ID=
TTPBOT_RACETIME_CLIENT_SECRET=
TTPBOT_Z1R_WEBHOOK_URL=
TTPBOT_DATA_DIR=/var/lib/ttpbot
```

- [ ] **Step 3: Validate service asset text**

Run:

```powershell
Select-String -Path deploy\\ttpbot.service -Pattern 'User=ttpbot','EnvironmentFile=/etc/ttpbot.env','ExecStart=/opt/ttpbot/.venv/bin/python -m ttpbot','ReadWritePaths=/var/lib/ttpbot'
Select-String -Path deploy\\ttpbot.env.example -Pattern 'TTPBOT_CATEGORY_SLUG','TTPBOT_RACETIME_CLIENT_ID','TTPBOT_RACETIME_CLIENT_SECRET','TTPBOT_Z1R_WEBHOOK_URL','TTPBOT_DATA_DIR'
```

Expected: all patterns are found.

- [ ] **Step 4: Commit**

```bash
git add deploy/ttpbot.service deploy/ttpbot.env.example
git commit -m "deploy: add systemd service for OCI"
```

## Task 5: OCI Runbook and README

**Files:**
- Create: `docs/oci-service-runbook.md`
- Modify: `README.md`

- [ ] **Step 1: Write OCI runbook**

Create `docs/oci-service-runbook.md` with these sections:

```markdown
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
```
```

- [ ] **Step 2: Update README**

Add a short Linux production section after the existing NSSM section:

```markdown
### Deployment (Linux systemd on OCI)

Production runs as a standalone `ttpbot.service` on the OCI `coop-relay` VM.
The Linux service reads credentials from `/etc/ttpbot.env` and writes runtime
state to `/var/lib/ttpbot` via `TTPBOT_DATA_DIR`.

See [docs/oci-service-runbook.md](docs/oci-service-runbook.md).
```

- [ ] **Step 3: Review docs for accidental secrets**

Run:

```powershell
Select-String -Path docs\\oci-service-runbook.md,deploy\\ttpbot.env.example,README.md -Pattern 'discord.com/api/webhooks','client_secret=','TTPBOT_RACETIME_CLIENT_SECRET=.'
```

Expected: no real webhook URL or client secret values are present.

- [ ] **Step 4: Commit**

```bash
git add docs/oci-service-runbook.md README.md
git commit -m "docs: add OCI service runbook"
```

## Task 6: Local Verification Before OCI

**Files:**
- No new files unless a previous task exposes a bug.

- [ ] **Step 1: Install package locally if needed**

Run:

```powershell
python -m pip install -e .
```

Expected: package installs with `racetime_bot` dependency available.

- [ ] **Step 2: Run all tests**

Run:

```powershell
python -m unittest discover -v
```

Expected: all tests pass.

- [ ] **Step 3: Compile package**

Run:

```powershell
python -m compileall ttpbot tests
```

Expected: exits 0.

- [ ] **Step 4: Verify CLI help**

Run:

```powershell
python -m ttpbot --help
```

Expected: exits 0.

- [ ] **Step 5: Verify environment startup reaches bot construction without args**

Do not use production credentials for this local dry check. Patch `TTPBot.run`
so no network connection starts:

```powershell
python -c "import os; os.environ.update({'TTPBOT_CATEGORY_SLUG':'z1r','TTPBOT_RACETIME_CLIENT_ID':'id','TTPBOT_RACETIME_CLIENT_SECRET':'secret'}); import ttpbot; from ttpbot.bot import TTPBot; TTPBot.run=lambda self: print(self.category_slug); ttpbot.main()"
```

Expected: prints `z1r` and exits 0.

- [ ] **Step 6: Verify data-dir writes with isolated temp directory**

Run:

```powershell
$tmp = New-Item -ItemType Directory -Force .tmp\\ttpbot-data-test
$env:TTPBOT_DATA_DIR = $tmp.FullName
python -m unittest tests.test_runtime_state_paths -v
Remove-Item Env:\\TTPBOT_DATA_DIR
```

Expected: PASS.

- [ ] **Step 7: Check Git status**

Run:

```powershell
git status --short
```

Expected: clean, unless intentionally holding the next deploy-only change.

## Task 7: Push Code for VM Deployment

**Files:**
- No source edits.

- [ ] **Step 1: Confirm local branch**

Run:

```powershell
git status --short
git log --oneline -5
```

Expected: clean tree and migration commits at the top.

- [ ] **Step 2: Push to GitHub**

Run:

```powershell
git push origin main
```

Expected: push succeeds. The VM deploy pulls from `https://github.com/BogieSmalls/TTPBot.git`.

## Task 8: Install TTPBot on OCI

**Files:**
- No repo files.
- Remote VM paths: `/opt/ttpbot`, `/var/lib/ttpbot`, `/etc/ttpbot.env`, `/etc/systemd/system/ttpbot.service`.

- [ ] **Step 1: Identify coop-relay SSH target**

Use the existing relay docs or OCI console to confirm the `coop-relay` public IP/hostname. Do not reuse the restream encoder VM.

- [ ] **Step 2: Install base packages on VM**

Run on the VM:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv git
```

Expected: packages install or are already current.

- [ ] **Step 3: Install or update app code**

Run on the VM:

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
```

Expected: package installs successfully.

- [ ] **Step 4: Install service unit**

Run on the VM:

```bash
sudo cp /opt/ttpbot/deploy/ttpbot.service /etc/systemd/system/ttpbot.service
sudo systemctl daemon-reload
```

Expected: no systemd errors.

- [ ] **Step 5: Create `/etc/ttpbot.env`**

Run on the VM:

```bash
sudo install -m 600 -o root -g root /opt/ttpbot/deploy/ttpbot.env.example /etc/ttpbot.env
sudoedit /etc/ttpbot.env
```

Set real values from the existing private credentials. Do not echo them in terminal output or commit them.

- [ ] **Step 6: Validate env file is populated without printing secrets**

Run on the VM:

```bash
sudo awk -F= '
  /^[A-Z0-9_]+=/{ if ($2 == "") { print "missing " $1; missing=1 } }
  END { exit missing ? 1 : 0 }
' /etc/ttpbot.env
```

Expected: exits 0 with no output.

## Task 9: Cut Over From Windows NSSM to OCI

**Files:**
- Runtime files only.

- [ ] **Step 1: Pick safe cutover window**

Use a time outside a TTP room-open window and when no active room needs bot handling.

- [ ] **Step 2: Stop Windows service**

Run from an elevated Windows shell:

```powershell
nssm stop TTPBot
```

Expected: service stops. Do not disable it yet.

- [ ] **Step 3: Copy state to VM**

Run from the Windows TTPBot directory:

```powershell
ssh ubuntu@<coop-relay-host> "rm -rf /tmp/ttpbot-state && mkdir -p /tmp/ttpbot-state"
scp created_races.json sent_webhooks.json learned_aliases.json ubuntu@<coop-relay-host>:/tmp/ttpbot-state/
scp -r chat_logs ubuntu@<coop-relay-host>:/tmp/ttpbot-state/
```

Expected: files copy successfully. If a JSON file is absent, skip that file and continue.

- [ ] **Step 4: Move state into `/var/lib/ttpbot`**

Run on the VM:

```bash
sudo install -d -o ttpbot -g ttpbot /var/lib/ttpbot
sudo cp /tmp/ttpbot-state/created_races.json /var/lib/ttpbot/ 2>/dev/null || true
sudo cp /tmp/ttpbot-state/sent_webhooks.json /var/lib/ttpbot/ 2>/dev/null || true
sudo cp /tmp/ttpbot-state/learned_aliases.json /var/lib/ttpbot/ 2>/dev/null || true
sudo cp -r /tmp/ttpbot-state/chat_logs /var/lib/ttpbot/ 2>/dev/null || true
sudo chown -R ttpbot:ttpbot /var/lib/ttpbot
sudo find /var/lib/ttpbot -maxdepth 2 -type f | sed 's#^#/state #'
```

Expected: state files are visible under `/var/lib/ttpbot`.

- [ ] **Step 5: Start cloud service**

Run on the VM:

```bash
sudo systemctl enable --now ttpbot
sudo systemctl status ttpbot --no-pager
```

Expected: service is active.

- [ ] **Step 6: Watch logs**

Run on the VM:

```bash
sudo journalctl -u ttpbot -n 100 --no-pager
```

Expected: no repeated exceptions. Racetime auth should succeed.

- [ ] **Step 7: Verify state writes**

Run on the VM:

```bash
sudo -u ttpbot test -w /var/lib/ttpbot
sudo ls -la /var/lib/ttpbot
```

Expected: directory is writable by `ttpbot`.

## Task 10: Production Verification and Windows Retirement

**Files:**
- Runtime files only.

- [ ] **Step 1: Verify next scheduled room behavior**

Observe the next scheduled TTP room-open window.

Expected:

- TTPBot creates the room once.
- `created_races.json` updates in `/var/lib/ttpbot`.
- Discord Race Seekers webhook posts once.
- `sent_webhooks.json` updates in `/var/lib/ttpbot`.

- [ ] **Step 2: Verify in-room behavior**

In a TTP room, confirm:

- Welcome message is present once.
- Reminders fire at the expected times.
- `!help`, `!schedule`, `!info`, and `!ttpflags` respond.
- Hash confirmation still works.
- Seed commands defer when SahasrahBot is present.
- Chat log appears under `/var/lib/ttpbot/chat_logs/`.

- [ ] **Step 3: Disable Windows NSSM service**

After cloud verification:

```powershell
nssm stop TTPBot
nssm set TTPBot Start SERVICE_DISABLED
```

Expected: Windows service is disabled and cloud remains active.

- [ ] **Step 4: Record final state**

Run:

```bash
sudo systemctl status ttpbot --no-pager
sudo journalctl -u ttpbot -n 50 --no-pager
```

Expected: service active, no repeated exceptions.

## Task 11: Rollback Drill Notes

**Files:**
- Runtime files only.

- [ ] **Step 1: Stop cloud before rollback**

Run on the VM:

```bash
sudo systemctl stop ttpbot
```

Expected: cloud bot is stopped.

- [ ] **Step 2: Copy dedupe state back if cloud had side effects**

Run from Windows:

```powershell
scp ubuntu@<coop-relay-host>:/var/lib/ttpbot/created_races.json .
scp ubuntu@<coop-relay-host>:/var/lib/ttpbot/sent_webhooks.json .
scp ubuntu@<coop-relay-host>:/var/lib/ttpbot/learned_aliases.json .
```

Expected: Windows receives current dedupe state before restart.

- [ ] **Step 3: Restart Windows service**

Run from elevated Windows shell:

```powershell
nssm set TTPBot Start SERVICE_AUTO_START
nssm start TTPBot
```

Expected: Windows bot resumes as the only active service.
