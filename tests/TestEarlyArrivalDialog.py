"""守卫：剧情「提前到达目标位置…是否确认前往」弹窗的通用处理。

两类回归：
1. wait_after_book_click 必须在等待队伍/传送界面期间处理该弹窗，
   否则点「直接挑战」后弹窗会卡满 10 秒并抛 WaitFailedException。
2. click_skip_dialog_confirm 必须先确认「跳过剧情」复选框存在，
   再找 confirm_btn；否则无复选框的弹窗每 0.5s 触发一次 find_one
   命中两个同款确认按钮，持续刷 "too many 2" 警告。
"""

import ast
import unittest
from pathlib import Path

SOURCE = Path('src/task/BaseWWTask.py').read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)


def _method(name):
    return next(n for n in ast.walk(TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name)


class TestEarlyArrivalDialog(unittest.TestCase):

    def test_handler_detects_story_prompt_text(self):
        cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'BaseWWTask')
        # 文案正则要覆盖简繁两种「确认前往」写法
        assign = next(n for n in cls.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == '_EARLY_ARRIVAL_TEXT'
                              for t in n.targets))
        pattern = ast.literal_eval(assign.value.args[0])
        self.assertRegex('提前到达目标位置可能影响剧情体验，是否确认前往？', pattern)
        self.assertRegex('是否確認前往', pattern)

    def test_wait_after_book_click_hooks_handler_as_pre_action(self):
        method = _method('wait_after_book_click')
        src = ast.get_source_segment(SOURCE, method)
        self.assertIn('pre_action=self.handle_early_arrival_confirm', src)

    def test_skip_dialog_checks_checkbox_before_confirm_button(self):
        method = _method('click_skip_dialog_confirm')
        src = ast.get_source_segment(SOURCE, method)
        first_checkbox = src.index('skip_dialog_check')
        first_confirm = src.index('confirm_btn_hcenter_vcenter')
        self.assertLess(first_checkbox, first_confirm,
                        '复选框检查必须在 confirm_btn 匹配之前，避免无复选框弹窗刷屏')

    def test_handler_clicks_confirm_via_ocr_or_fallback(self):
        method = _method('handle_early_arrival_confirm')
        src = ast.get_source_segment(SOURCE, method)
        self.assertIn('click_box(confirm', src)
        self.assertIn('self.click(0.66, 0.65)', src)


if __name__ == '__main__':
    unittest.main()
