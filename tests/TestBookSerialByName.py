"""serial 查找优先按序号换算的名字匹配，抗 OCR 漏读/乱序。

复现 2026-11-21 实机 bug：周本 serial=5，OCR 只读到 3 行（顺序还是乱的：
烬夜天启之章、定序诸理之律、失坠困咎之庭），旧逻辑数第 5 行必然 not found；
目标「烬夜天启之章」其实已被读到，按名字匹配第一页就能命中。
"""

import unittest
from unittest.mock import Mock

from src.task.BaseWWTask import BaseWWTask


def _row(name, with_btn=True):
    return (name, 0.5, Mock() if with_btn else None, False)


class TestPickBookRowBySerialName(unittest.TestCase):
    def test_serial_name_hit_when_only_few_rows_read(self):
        # OCR 这一页只读到 3 行，且顺序与列表序号不一致
        rows = [_row('烬夜天启之章·战歌重奏'),
                _row('定序诸理之律·战歌重奏'),
                _row('失坠困咎之庭·战歌重奏')]
        hit = BaseWWTask._pick_book_row(rows, 'zhange', '', 5, [], set())
        self.assertIsNotNone(hit)
        self.assertIn('烬夜天启之章', hit[0])

    def test_serial_name_tolerates_ocr_typo(self):
        rows = [_row('烬夜天启之卓')]  # OCR 末字误读「章」→「卓」，容 1 错字
        hit = BaseWWTask._pick_book_row(rows, 'zhange', '', 5, [], set())
        self.assertIsNotNone(hit)

    def test_falls_back_to_counting_when_name_not_on_page(self):
        # serial=2 换算名「定序诸理之律」不在本页，回退数第 2 个 seen 行（失坠困咎之庭）
        rows = [_row('虚妄诞生之种'), _row('失坠困咎之庭')]
        seen = ['定序诸理之律', '失坠困咎之庭', '虚妄诞生之种']
        hit = BaseWWTask._pick_book_row(rows, 'zhange', '', 2, seen, set())
        self.assertIsNotNone(hit)
        self.assertIn('失坠困咎之庭', hit[0])

    def test_explicit_target_never_falls_back_to_counting(self):
        # 明确给了名字，匹配不到就返回 None，不按序号乱点
        rows = [_row('虚妄诞生之种')]
        hit = BaseWWTask._pick_book_row(rows, 'zhange', '烬夜天启之章', 5, [], set())
        self.assertIsNone(hit)

    def test_no_button_row_not_clicked(self):
        rows = [_row('烬夜天启之章', with_btn=False)]
        hit = BaseWWTask._pick_book_row(rows, 'zhange', '', 5, [], set())
        self.assertIsNone(hit)


if __name__ == '__main__':
    unittest.main()
