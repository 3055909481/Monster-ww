"""Source guards for the stuck-combat and stop-not-honoured fixes.

Both modules import the Mon framework (unavailable locally), so the key
invariants are locked with AST/source checks. CI runs the full suite.
"""

import ast
import unittest
from pathlib import Path


class StuckCombatTest(unittest.TestCase):
    def setUp(self):
        self.source = Path('src/char/ShoreKeeper.py').read_text(encoding='utf-8')

    def test_intro_skip_is_time_bounded(self):
        fn = next(n for n in ast.walk(ast.parse(self.source))
                  if isinstance(n, ast.FunctionDef) and n.name == 'skip_combat_check')
        body = ast.unparse(fn)
        self.assertIn('INTRO_COMBAT_CHECK_GRACE', body)
        self.assertIn('last_switch_in_time', body)
        self.assertIn('self.has_intro = False', body)  # stale flag must clear
        self.assertIn('return self.flying()', body)

    def test_grace_constant_is_finite(self):
        cls = next(n for n in ast.walk(ast.parse(self.source))
                   if isinstance(n, ast.ClassDef) and n.name == 'ShoreKeeper')
        assign = next(n for n in cls.body if isinstance(n, ast.Assign)
                      and any(getattr(t, 'id', None) == 'INTRO_COMBAT_CHECK_GRACE' for t in n.targets))
        value = ast.literal_eval(assign.value)
        self.assertIsInstance(value, (int, float))
        self.assertGreater(value, 0)
        self.assertLessEqual(value, 30)


class MultiAccountStopTest(unittest.TestCase):
    def setUp(self):
        self.source = Path('src/task/MultiAccountConfigTask.py').read_text(encoding='utf-8')

    def test_stop_check_exists_and_guards_click(self):
        self.assertIn('def _check_stopped(self):', self.source)
        self.assertIn('if not self.enabled:', self.source)
        self.assertIn('raise TaskDisabledException', self.source)
        click = next(n for n in ast.walk(ast.parse(self.source))
                     if isinstance(n, ast.FunctionDef) and n.name == 'click')
        self.assertIn('_check_stopped()', ast.unparse(click))

    def test_stop_checked_before_each_account(self):
        loop = next(n for n in ast.walk(ast.parse(self.source))
                    if isinstance(n, ast.For)
                    and 'account' in ast.unparse(n.target))
        self.assertIn('self._check_stopped()', ast.unparse(loop))


if __name__ == '__main__':
    unittest.main()
