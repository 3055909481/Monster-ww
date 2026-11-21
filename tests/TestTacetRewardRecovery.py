"""无音区领奖恢复与重新挑战识别（来自 xjtu-ww fork d1fe350）。"""

import unittest
from unittest.mock import Mock

from Mon.task.exceptions import WaitFailedException
from src.task.TacetTask import TacetTask


def make_task():
    task = TacetTask.__new__(TacetTask)
    task.combat_once = Mock()
    task.walk_to_treasure = Mock()
    task.in_realm = Mock(return_value=True)
    task.wait_combat = Mock(return_value=True)
    return task


class TestClearTacetAndReachReward(unittest.TestCase):
    def test_walks_to_treasure_after_combat(self):
        task = make_task()
        task.clear_tacet_and_reach_reward()
        task.combat_once.assert_called_once_with(target=True)
        task.walk_to_treasure.assert_called_once_with()

    def test_resumes_combat_when_reward_timeout_but_arena_active(self):
        task = make_task()
        task.walk_to_treasure.side_effect = [WaitFailedException(), None]
        task.clear_tacet_and_reach_reward()
        # 一次常规 + 一次续战
        self.assertEqual(2, task.combat_once.call_count)
        self.assertEqual(2, task.walk_to_treasure.call_count)
        task.wait_combat.assert_called_once_with(target=True, time_out=5, raise_if_not_found=False)

    def test_reraises_when_reward_missing_and_arena_inactive(self):
        task = make_task()
        task.walk_to_treasure.side_effect = WaitFailedException()
        task.wait_combat.return_value = False
        with self.assertRaises(WaitFailedException):
            task.clear_tacet_and_reach_reward()
        self.assertEqual(1, task.combat_once.call_count)

    def test_reraises_when_not_in_realm(self):
        task = make_task()
        task.walk_to_treasure.side_effect = WaitFailedException()
        task.in_realm.return_value = False
        with self.assertRaises(WaitFailedException):
            task.clear_tacet_and_reach_reward()
        task.wait_combat.assert_not_called()


class TestRechallengeTacet(unittest.TestCase):
    def test_clicks_ocr_detected_button(self):
        task = make_task()
        button = Mock()
        task.ocr = Mock(return_value=[button])
        task.wait_until = lambda condition, **kwargs: condition()
        task.click = Mock()
        task.click_relative = Mock()
        task.wait_click_skip_dialog_confirm = Mock()

        task.rechallenge_tacet()

        task.click.assert_called_once_with(button, after_sleep=0.2)
        task.click_relative.assert_not_called()
        task.wait_click_skip_dialog_confirm.assert_called_once()

    def test_falls_back_to_fixed_coordinate_when_no_ocr_match(self):
        task = make_task()
        task.ocr = Mock(return_value=[])
        task.wait_until = lambda condition, **kwargs: None
        task.click = Mock()
        task.click_relative = Mock()
        task.wait_click_skip_dialog_confirm = Mock()

        task.rechallenge_tacet()

        task.click.assert_not_called()
        task.click_relative.assert_called_once_with(0.640, 0.851, hcenter=True, after_sleep=0.2)
        task.wait_click_skip_dialog_confirm.assert_called_once()


if __name__ == '__main__':
    unittest.main()
