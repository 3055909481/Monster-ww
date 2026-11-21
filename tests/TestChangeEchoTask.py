"""声骸主属性精确匹配：目标「暴击」不能误匹配「暴击伤害」，
OCR 的「主属性： 暴 击」（带空格）要能正确识别为同一属性。
"""

import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.task.ChangeEchoTask import ChangeEchoTask


def make_task(current='暴击伤害', target='暴击'):
    info = {}
    task = SimpleNamespace(config={'目标属性': target}, debug=False, tr=str)
    task.find_echo_enhance = Mock(side_effect=[object(), None, object()])
    task.is_0_level = Mock(side_effect=[True, False])
    task.info_set = lambda key, value: info.__setitem__(key, value)
    task.info_get = info.get
    task.info_incr = lambda key: info.__setitem__(key, info.get(key, 0) + 1)
    for name in ('sleep', 'click', 'wait_click_ocr', 'esc', 'log_info', 'log_error'):
        setattr(task, name, Mock())
    choices = [SimpleNamespace(name='主属性 暴击伤害'), SimpleNamespace(name='主属性： 暴 击')]

    def ocr(*args, match=None, **kwargs):
        if isinstance(match, re.Pattern):
            return [box for box in choices if match.search(box.name)]
        if match is None:
            return [SimpleNamespace(name=current)]
        return [SimpleNamespace(name=match)]

    task.wait_ocr = Mock(side_effect=ocr)
    return task, info, choices


class TestChangeEchoTask(unittest.TestCase):
    def test_crit_target_does_not_match_crit_damage(self):
        # 目标「暴击」不应误判为与当前「暴击伤害」相同，应选择列表里的「暴 击」
        task, _, choices = make_task()
        with patch('src.task.ChangeEchoTask.os.startfile'):
            ChangeEchoTask.run(task)
        selections = [entry.args[0] for entry in task.click.call_args_list
                      if entry.args and isinstance(entry.args[0], list)]
        self.assertEqual(selections, [[choices[1]]])
        task.esc.assert_called_once_with()

    def test_equal_stat_with_ui_prefix_and_spaces_stops_before_reconstruction(self):
        # 当前属性 OCR 成「 主属性： 暴 击 」时，应识别为与目标相同并提前停止
        task, _, _ = make_task(current=' 主属性： 暴 击 ')
        with self.assertRaises(Exception):
            ChangeEchoTask.run(task)
        task.wait_click_ocr.assert_not_called()
        task.esc.assert_not_called()

    def test_completion_counts_success_and_failure_with_missing_key(self):
        # 我方计数 = 成功 + 失败（比 xjtu 更完整）。失败键未初始化返回 None
        # 时必须兜底为 0，否则 3 + None 抛 TypeError（回归保护）。
        task, info, _ = make_task()
        info['成功声骸数量'] = 3  # 故意不设「失败声骸数量」
        task.is_0_level.return_value = False
        task.is_0_level.side_effect = None
        with patch('src.task.ChangeEchoTask.os.startfile'):
            ChangeEchoTask.run(task)  # 不应抛异常
        task.wait_click_ocr.assert_not_called()


if __name__ == '__main__':
    unittest.main()
