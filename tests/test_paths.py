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
