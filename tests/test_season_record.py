import asyncio
import copy
import json
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from ttpbot.config import POST_SEASON_GOAL_NAME, TIMEZONE
from ttpbot.handler import TTPRaceHandler
from ttpbot.room_policy import is_ttp_scheduled_room
from ttpbot.schedule import (
    get_races_for_date,
    is_scheduled_date,
    is_within_season,
    race_goal_for_time,
    race_info_for_time,
    slot_name,
)
from ttpbot.season_record import BUILT_IN, SeasonRecord, flags_text, info_text, seasons, validate_snapshot, welcome_text
from tests.test_handler_commands import command_handler

CONTRACT = json.loads((Path(__file__).parent / 'fixtures' / 'ttp-seasons-contract.json').read_text(encoding='utf-8'))
S5 = CONTRACT['base']['seasons'][0]

# Copied from origin/main's handler, before it read the record.
WELCOME_5 = ("Welcome to TTP Season 5! I'll help out with hash "
             "confirmation and other bot duties. "
             "Type !schedule for today's race times, !info for TTP details, "
             "or !ttpflags for flagset details.")
INFO_5 = ("TTP Season 5 regular season runs Aug 31 - Dec 19, 2026. "
          "Rooms use the TTP Season 5 goal during this window. "
          "Races: Mon-Sat at 8 PM, 10 PM, 12 AM ET, plus 6 PM on Saturday "
          "(the 12 AM race closes out the previous evening). "
          "No Sunday evening races.")
FLAGS_5 = ("TTP5 flagset presets:\n"
           "  !ttp5 -- Random pick from the three TTP5 flagsets\n"
           "  !ttp5uphill -- Uphill Battle\n"
           "  !ttp5muffle -- Muffle Rug\n"
           "  !ttp5pick5 -- Pick 5\n")


def season_6(**changes):
    season = copy.deepcopy(S5)
    season.update({
        'number': 6, 'theme': 'Six', 'goal': 'TTP Season 6', 'roomPrefix': 'TTP Season 6', 'boardLabel': 'TTP S6',
        'schedule': {'firstNight': '2027-03-01', 'lastNight': '2027-06-19', 'weeks': 16, 'afterLastRace': 'stop',
                     'slots': [{'name': 'TTP1', 'at': '19:30', 'days': [1, 2, 3, 4, 5]},
                               {'name': 'TTPS', 'at': '15:00', 'days': [6]}]},
        'flagsets': [{'name': 'TTP6 Alpha', 'command': 'ttp6alpha'}, {'name': 'TTP6 Beta', 'command': 'ttp6beta'},
                     {'name': 'TTP6 Gamma', 'command': 'ttp6gamma'}],
        'randomCommand': 'ttp6',
    })
    season.update(changes)
    return season


def snapshot(*extra, revision=2):
    return {'schemaVersion': 1, 'revision': revision, 'updatedAt': '2026-11-06T17:00:00.000Z', 'seasons': [copy.deepcopy(S5), *extra]}


def all_races_in_season(season):
    first = date.fromisoformat(season['schedule']['firstNight'])
    last = date.fromisoformat(season['schedule']['lastNight'])
    races = []
    for offset in range((last - first).days + 2):
        races.extend(get_races_for_date(first + timedelta(days=offset)))
    return races


def apply_change(value, change):
    changed = copy.deepcopy(value)
    for dotted, replacement in change.items():
        target = changed
        keys = dotted.split('.')
        for key in keys[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        last = keys[-1]
        if isinstance(target, list):
            target[int(last)] = replacement
        else:
            target[last] = replacement
    return changed


class RecordCase(unittest.TestCase):
    def use(self, record):
        self.addCleanup(setattr, seasons, '_seasons', seasons._seasons)
        seasons.install(record)


class ContractTests(unittest.TestCase):
    def test_the_shared_contract_file_passes(self):
        self.assertEqual(validate_snapshot(CONTRACT['base']), [])

    def test_every_rejected_change_is_rejected_for_its_reason(self):
        for row in CONTRACT['invalid']:
            with self.subTest(why=row['why']):
                season = apply_change(S5, row['change'])
                problems = validate_snapshot({**CONTRACT['base'], 'seasons': [season]})
                self.assertTrue(any(row['problem'] in problem for problem in problems), problems)

    def test_the_built_in_copy_is_the_contract_season(self):
        self.assertEqual(BUILT_IN, S5)


class ScheduleTests(RecordCase):
    def test_last_room_is_sunday_midnight(self):
        races = all_races_in_season(BUILT_IN)
        self.assertEqual(len(races), 304)
        self.assertEqual(races[-1], datetime(2026, 12, 20, 0, 0, tzinfo=TIMEZONE))

    def test_nothing_is_scheduled_after_the_last_race(self):
        self.assertTrue(is_scheduled_date(date(2026, 12, 19)))
        self.assertFalse(is_scheduled_date(date(2026, 12, 20)))
        self.assertFalse(is_scheduled_date(date(2026, 12, 21)))
        self.assertEqual(get_races_for_date(date(2026, 12, 21)), [])
        self.assertEqual(get_races_for_date(date(2027, 2, 1)), [])

    def test_continue_keeps_opening_on_beat_the_game(self):
        season = copy.deepcopy(S5)
        season['schedule']['afterLastRace'] = 'continue'
        self.use({**snapshot(), 'seasons': [season]})
        self.assertTrue(is_scheduled_date(date(2026, 12, 21)))
        monday = get_races_for_date(date(2026, 12, 21))
        self.assertEqual([race.time() for race in monday], [time(20, 0), time(22, 0)])
        self.assertEqual(race_goal_for_time(monday[0]), POST_SEASON_GOAL_NAME)
        self.assertIn('Triforce Triple Play | Scheduled:', race_info_for_time(monday[0]))

    def test_a_season_6_record_changes_goal_label_and_info(self):
        self.use(snapshot(season_6()))
        tuesday = get_races_for_date(date(2027, 3, 2))
        self.assertEqual([race.time() for race in tuesday], [time(19, 30)])
        self.assertEqual(race_goal_for_time(tuesday[0]), 'TTP Season 6')
        self.assertIn('TTP Season 6 | Scheduled:', race_info_for_time(tuesday[0]))
        self.assertEqual(slot_name(tuesday[0]), 'TTP1')
        self.assertEqual(slot_name(get_races_for_date(date(2027, 3, 6))[0]), 'TTPS')
        self.assertTrue(is_within_season(tuesday[0]))
        info = info_text(seasons.current(tuesday[0]))
        self.assertIn('TTP Season 6 regular season runs Mar 1 - Jun 19, 2027.', info)
        self.assertIn('Mon-Fri at 7:30 PM ET, plus 3 PM on Saturday', info)
        self.assertNotIn('closes out', info)
        self.assertIn('Welcome to TTP Season 6!', welcome_text(seasons.current(tuesday[0])))
        self.assertIn('!ttp6 -- Random pick from the three TTP6 flagsets', flags_text(seasons.current(tuesday[0])))
        self.assertIn('!ttp6alpha -- Alpha', flags_text(seasons.current(tuesday[0])))

    def test_season_5_still_opens_nothing_while_season_6_is_only_on_file(self):
        self.use(snapshot(season_6()))
        self.assertEqual(get_races_for_date(date(2026, 12, 21)), [])
        self.assertEqual(race_goal_for_time(datetime(2026, 12, 19, 22, 0, tzinfo=TIMEZONE)), 'TTP Season 5')

    def test_a_room_of_any_season_on_file_is_a_ttp_room(self):
        self.use(snapshot(season_6()))
        self.assertTrue(is_ttp_scheduled_room({'goal': {'name': 'TTP Season 5'}}))
        self.assertTrue(is_ttp_scheduled_room({'goal': {'name': 'TTP Season 6'}}))
        self.assertFalse(is_ttp_scheduled_room({'goal': {'name': 'TTP Season 7'}}))
        self.assertTrue(is_ttp_scheduled_room({'goal': {'name': POST_SEASON_GOAL_NAME}, 'info_bot': 'TTP Season 6 | Scheduled: x'}))
        self.assertTrue(is_ttp_scheduled_room({'goal': {'name': POST_SEASON_GOAL_NAME}, 'info_bot': 'Triforce Triple Play | Scheduled: x'}))
        self.assertFalse(is_ttp_scheduled_room({'goal': {'name': POST_SEASON_GOAL_NAME}, 'info_bot': 'League: x'}))


class CopyTests(RecordCase):
    def test_welcome_info_and_labels_are_unchanged_for_season_5(self):
        season = seasons.current(datetime(2026, 11, 7, 20, 0, tzinfo=TIMEZONE))
        self.assertEqual(welcome_text(season), WELCOME_5)
        self.assertEqual(info_text(season), INFO_5)
        self.assertTrue(flags_text(season).startswith(FLAGS_5))
        self.assertEqual(slot_name(datetime(2026, 11, 7, 18, 0, tzinfo=TIMEZONE)), 'TTP0')
        self.assertEqual(slot_name(datetime(2026, 11, 8, 0, 0, tzinfo=TIMEZONE)), 'TTP3')
        self.assertIsNone(slot_name(datetime(2026, 11, 9, 18, 0, tzinfo=TIMEZONE)), 'a weekday 6 PM is nobody')

    def test_the_random_command_follows_the_record(self):
        self.use(snapshot(season_6()))
        handler = command_handler()
        handler._roll_unknown_to_sahasrahbot = AsyncMock()
        handler._log_chat = Mock()
        asyncio.run(handler.chat_message({'message': {'message': '!ttp6', 'user': {'name': 'someone'}}}))
        preset = handler._roll_unknown_to_sahasrahbot.await_args.args[0]
        self.assertIn(preset, ['ttp6alpha', 'ttp6beta', 'ttp6gamma'])

    def test_help_names_each_season_on_file(self):
        self.use(snapshot(season_6()))
        handler = command_handler()
        lines = handler._season_help_lines()
        self.assertIn('    !ttp5                       Random TTP Season 5 preset', lines)
        self.assertIn('    !ttp5uphill / !ttp5muffle / !ttp5pick5  TTP5 presets directly', lines)
        self.assertIn('    !ttp6                       Random TTP Season 6 preset', lines)


class FileTests(unittest.TestCase):
    def fake_session(self, text, status=200):
        response = AsyncMock()
        response.status = status
        response.text = AsyncMock(return_value=text)
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = MagicMock()
        session.get.return_value = context
        return session

    def test_a_bad_file_keeps_the_last_good_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'ttp-seasons.json'
            record = SeasonRecord('https://example.test/ttp-seasons.json', cache, BUILT_IN)
            good = json.dumps(snapshot(season_6()))
            asyncio.run(record.refresh(self.fake_session(good)))
            self.assertTrue(record.loaded)
            self.assertEqual(cache.read_text(encoding='utf-8'), good)
            for bad in ['{not json', json.dumps({'schemaVersion': 2}), json.dumps({**snapshot(), 'seasons': []})]:
                with self.subTest(bad=bad[:20]), self.assertRaises(Exception):
                    asyncio.run(record.refresh(self.fake_session(bad)))
                self.assertEqual(sorted(record.goals()), ['TTP Season 5', 'TTP Season 6'])
                self.assertEqual(cache.read_text(encoding='utf-8'), good)
            with self.assertRaises(RuntimeError):
                asyncio.run(record.refresh(self.fake_session('', status=502)))
            self.assertEqual(sorted(record.goals()), ['TTP Season 5', 'TTP Season 6'])

    def test_a_restart_reads_the_cache_and_a_bad_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'ttp-seasons.json'
            cache.write_text(json.dumps(snapshot(season_6())), encoding='utf-8')
            record = SeasonRecord(None, cache, BUILT_IN)
            record.load_cache()
            self.assertEqual(sorted(record.goals()), ['TTP Season 5', 'TTP Season 6'])
            cache.write_text('{bad', encoding='utf-8')
            fresh = SeasonRecord(None, cache, BUILT_IN)
            fresh.load_cache()
            self.assertFalse(fresh.loaded)
            self.assertEqual(fresh.goals(), ['TTP Season 5'])

    def test_no_url_is_not_an_error(self):
        record = SeasonRecord(None, None, BUILT_IN)
        with patch('ttpbot.season_record.logger.info') as info:
            asyncio.run(record.run())
        self.assertIn('no Z1RR_SEASONS_URL', info.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
