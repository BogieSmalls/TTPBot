import unittest
from unittest.mock import AsyncMock, patch

from ttpbot.config import SEED_PRESETS
from ttpbot.handler import TTPRaceHandler

from tests.test_handler_commands import command_handler

CONSTERNATION = SEED_PRESETS['consternation']


def say(text, name='Bogie'):
    return {'message': {'message': text, 'message_plain': text, 'user': {'name': name}}}


class TTPBotRollingTests(unittest.IsolatedAsyncioTestCase):
    """What went wrong in the Autumn W1-30 room on 2026-10-02."""

    async def test_a_sentence_is_not_rolled_as_a_flag_string(self):
        # "!flags doesn't work either" rolled a seed for the flags "doesn't".
        handler = command_handler()

        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.chat_message(say("!flags doesn't work either"))

        self.assertFalse(handler.seed_rolled)
        self.assertEqual(handler.messages, [
            '"doesn\'t" is not a flag string. Usage: !flags <flagstring>',
        ])

    async def test_a_preset_name_works_as_a_command(self):
        handler = command_handler()

        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.chat_message(say('!consternation'))

        self.assertTrue(handler.messages[0].startswith(
            f'consternation - Flags: {CONSTERNATION} Seed: '))

    async def test_preset_shorthand_posts_the_flags_with_sahasrahbot_present(self):
        # SahasrahBot has no !consternation, so silence answered nobody.
        handler = command_handler()
        handler.sahasrahbot_present = True

        await handler.chat_message(say('!consternation'))

        self.assertFalse(handler.seed_rolled)
        self.assertEqual(handler.messages, [
            f'consternation flags: {CONSTERNATION} -- roll with !flags {CONSTERNATION}',
        ])

    async def test_cancel_clears_the_seed_so_a_new_one_can_be_rolled(self):
        handler = command_handler()
        handler.seed_rolled = True

        await handler.ex_cancel([], {})
        with patch('ttpbot.handler.asyncio.sleep', new=AsyncMock()):
            await handler.ex_flags([CONSTERNATION], {})

        self.assertEqual(handler.messages[0], 'Seed cleared. You may now roll a new one.')
        self.assertTrue(handler.messages[1].startswith('Seed: '))

    async def test_cancel_is_left_to_sahasrahbot_when_it_is_rolling(self):
        handler = command_handler()
        handler.sahasrahbot_present = True
        handler.seed_rolled = True

        await handler.ex_cancel([], {})

        self.assertEqual(handler.messages, [])
        self.assertTrue(handler.seed_rolled)

    def test_override_is_not_a_ttpbot_command(self):
        # SahasrahBot's !override waives the stream requirement; both bots
        # answered it.
        self.assertFalse(hasattr(TTPRaceHandler, 'ex_override'))
        self.assertTrue(hasattr(TTPRaceHandler, 'ex_ttpbot'))


if __name__ == '__main__':
    unittest.main()
