"""The zora service's deploy files, and that the z1r service's are untouched by them."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from ttpbot import preflight
from ttpbot.provider import RacetimeProvider
from ttpbot.runtime_config import resolve_bot_config
from ttpbot.zora import startup_problems

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'deploy'


def env_file(path):
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            key, _, value = line.partition('=')
            values[key] = value
    return values


class ZoraServiceTests(unittest.TestCase):
    def test_its_own_environment_state_and_lock(self):
        service = (DEPLOY / 'ttpbot-zora.service').read_text(encoding='utf-8')
        for line in ('EnvironmentFile=/etc/ttpbot-zora.env',
                     'ExecStart=/usr/bin/flock --nonblock /run/ttpbot-zora/scheduler.lock '
                     '/opt/ttpbot/.venv/bin/python -m ttpbot',
                     'RuntimeDirectory=ttpbot-zora', 'StateDirectory=ttpbot-zora',
                     'ReadWritePaths=/var/lib/ttpbot-zora', 'Restart=always', 'User=ttpbot'):
            self.assertIn(line + '\n', service)
        self.assertNotIn('/etc/ttpbot.env', service)
        self.assertNotIn('/var/lib/ttpbot\n', service)

    def test_the_env_example_is_a_zora_process_that_may_start(self):
        env = env_file(DEPLOY / 'ttpbot-zora.env.example')
        self.assertEqual(env['TTPBOT_CATEGORY_SLUG'], 'zora')
        self.assertEqual(env['TTPBOT_DATA_DIR'], '/var/lib/ttpbot-zora')
        # The zora category's bot is named Z1RRbot under Manage bots.
        self.assertEqual(env['TTPBOT_RACETIME_BOT_NAME'], 'Z1RRbot')
        self.assertEqual(set(env), {
            'TTPBOT_RACETIME_ORIGIN', 'TTPBOT_CATEGORY_SLUG', 'TTPBOT_RACETIME_CLIENT_ID',
            'TTPBOT_RACETIME_CLIENT_SECRET', 'TTPBOT_RACETIME_BOT_NAME', 'TTPBOT_DATA_DIR', 'TTPBOT_ENVIRONMENT',
            'ZORA_PRESETS_URL', 'ZORA_PRESETS_CACHE',
        })
        config = resolve_bot_config(SimpleNamespace(), {**env, 'TTPBOT_RACETIME_CLIENT_ID': 'id',
                                                        'TTPBOT_RACETIME_CLIENT_SECRET': 'secret'})
        self.assertEqual(config.provider.destination_key, 'https://racetime.gg|zora')
        self.assertEqual(startup_problems(env, config), [])

    def test_the_z1r_files_say_nothing_of_zora(self):
        for name in ('ttpbot.service', 'ttpbot.env.example', 'ttpbot-preflight'):
            self.assertNotIn('zora', (DEPLOY / name).read_text(encoding='utf-8').lower(), name)


    def test_the_runbooks_first_preflight_has_its_state_directory(self):
        # On a fresh host /var/lib/ttpbot-zora does not exist until a unit with
        # StateDirectory=ttpbot-zora runs, and the preflight runs before the service.
        runbook = (ROOT / 'docs' / 'oci-service-runbook.md').read_text(encoding='utf-8')
        zora = runbook[runbook.index('## ZORA category'):]
        preflight = next(line for line in zora.splitlines() if 'systemd-run' in line)
        self.assertIn('-p EnvironmentFile=/etc/ttpbot-zora.env', preflight)
        self.assertIn('-p StateDirectory=ttpbot-zora', preflight)

class PreflightLockTests(unittest.TestCase):
    def lock(self, category):
        return preflight._scheduler_lock(SimpleNamespace(provider=RacetimeProvider('https://racetime.gg', category)))

    def test_each_service_checks_its_own_lock(self):
        self.assertEqual(self.lock('z1r'), '/run/ttpbot/scheduler.lock')
        self.assertEqual(self.lock('z1rr'), '/run/ttpbot/scheduler.lock')
        self.assertEqual(self.lock('zora'), '/run/ttpbot-zora/scheduler.lock')
        self.assertEqual(preflight._scheduler_lock(SimpleNamespace(provider=None)), '/run/ttpbot/scheduler.lock')


if __name__ == '__main__':
    unittest.main()
