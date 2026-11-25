"""Tests for the ported 跑轴记录 (axis recorder) module.

Covers the Mon-framework adaptation: patch points resolve, config registers the
task/tab, no stale `ok` imports remain, and the JSONL data model parses events.
No real patching or GUI is needed.
"""

import json
import unittest
from pathlib import Path

import config
from src.recorder.AxisRecorder import PATCH_POINTS, _missing_patch_points
from src.recorder.runs import RunData


ROOT = Path(__file__).resolve().parent.parent


class TestRecorderWiring(unittest.TestCase):
    def test_all_patch_points_exist(self):
        self.assertEqual([], _missing_patch_points())

    def test_patch_points_target_mon_framework(self):
        keys = list(PATCH_POINTS)
        self.assertIn(('Mon.task.task', 'FindFeature'), keys)
        self.assertIn(('Mon.task.task', 'ExecutorOperation'), keys)
        # Application patch points stay under src.*
        self.assertIn(('src.char.BaseChar', 'BaseChar'), keys)
        self.assertIn(('src.combat.CombatCheck', 'CombatCheck'), keys)

    def test_config_registers_recorder_task_and_tab(self):
        triggers = [cls for _, cls in config.config['trigger_tasks']]
        tabs = [cls for _, cls in config.config['custom_tabs']]
        self.assertIn('AxisRecorderTask', triggers)
        self.assertIn('AxisRecorderTab', tabs)

    def test_no_stale_ok_framework_imports(self):
        for path in (ROOT / 'src' / 'recorder').glob('*.py'):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('from ok import', text, path.name)
            self.assertNotIn('from ok.', text, path.name)
            self.assertNotIn("logging.getLogger('ok')", text, path.name)

    def test_recorder_source_stays_python_for_patching(self):
        import setup as project_setup
        self.assertIn('recorder.py', project_setup.EXCLUDE_FILES)


class TestRecorderDataModel(unittest.TestCase):
    def test_run_data_parses_events(self):
        events = [
            {'e': 'in', 't': 0.1, 'key': 'W'},
            {'e': 'field', 't': 0.2, 'char': 'Aemeath'},
            {'e': 'log', 't': 0.3, 'text': 'aemeath burst: R1 not cast after 6.0s, moving on'},
        ]
        end = {'e': 'end', 't': 6.0}
        run = RunData('unused.jsonl', parsed=({'team': [{'i': 0, 'n': 'Aemeath'}]}, end, events))
        self.assertEqual(3, len(run.events))
        self.assertEqual(6.0, run.duration)
        self.assertEqual('Aemeath', run.names[0])


class TestWorkshopBundle(unittest.TestCase):
    def setUp(self):
        self.index = json.loads((ROOT / 'workshop_bundle' / 'teams.json').read_text(encoding='utf-8'))

    def test_index_matches_zip_files(self):
        listed = {c['filename'] for team in self.index['teams'] for c in team['codes']}
        actual = {p.name for p in (ROOT / 'workshop_bundle' / 'codes').glob('*.zip')}
        self.assertEqual(listed, actual)
        self.assertEqual(len(listed), self.index['totalCodes'])

    def test_aemeath_mon1_fix_included(self):
        aemeath = next(t for t in self.index['teams'] if t['slug'] == 'Aemeath_Lynae_Mornye')
        names = {c['filename'] for c in aemeath['codes']}
        self.assertIn('Aemeath_Lynae_Mornye_楽山_3.2.0-mon1.zip', names)


if __name__ == '__main__':
    unittest.main()
