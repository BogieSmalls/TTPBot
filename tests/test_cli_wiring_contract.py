"""Every resolved setting reaches the bot.

A flag that is read from the environment, validated, carried on the config
object and then never passed to TTPBot is invisible: the feature is simply off,
and nothing logs a reason. That happened to league_results_enabled, which was
switched on in production and did nothing for a day. This is the guard.
"""
import ast
import inspect
from pathlib import Path
import unittest

from ttpbot.bot import TTPBot
from ttpbot.runtime_config import BotRuntimeConfig

ROOT = Path(__file__).resolve().parents[1]


def _forwarded_kwargs():
    """The keyword arguments ttpbot/__init__.py passes to TTPBot()."""
    tree = ast.parse((ROOT / "ttpbot" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name == "TTPBot":
            return {keyword.arg for keyword in node.keywords if keyword.arg}
    raise AssertionError("ttpbot/__init__.py never constructs TTPBot")


class CliWiringContractTests(unittest.TestCase):
    def test_every_shared_setting_is_passed_to_the_bot(self):
        config_fields = {
            name for name in BotRuntimeConfig.__dataclass_fields__
            if not name.startswith("_")
        }
        accepted = set(inspect.signature(TTPBot.__init__).parameters) - {"self"}
        forwarded = _forwarded_kwargs()

        # A setting the bot accepts and the config resolves has to be handed
        # over; anything else is a feature that silently does nothing.
        shared = config_fields & accepted
        missing = sorted(shared - forwarded)
        self.assertEqual(
            missing, [],
            'ttpbot/__init__.py resolves {} but never passes it to TTPBot'.format(missing),
        )

    def test_results_flag_in_particular(self):
        self.assertIn("league_results_enabled", _forwarded_kwargs())


if __name__ == "__main__":
    unittest.main()
