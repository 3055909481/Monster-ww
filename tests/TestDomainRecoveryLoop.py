import ast
import unittest
from pathlib import Path


class TestDomainRecoveryLoop(unittest.TestCase):
    def setUp(self):
        module = ast.parse(Path("src/task/DomainTask.py").read_text(encoding="utf-8"))
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "DomainTask"
        )
        self.method_node = next(
            node for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == "farm_domain_with_recovery_loop"
        )

    def test_method_has_retry_parameter_with_default(self):
        # Locate max_recovery_retries by name: other optional parameters
        # (e.g. max_runs) may follow it, so it is not necessarily the last arg.
        args = self.method_node.args.args
        defaults = self.method_node.args.defaults
        names = [a.arg for a in args]
        self.assertIn("max_recovery_retries", names)
        # Defaults align with the tail of args.
        default_by_name = dict(zip(names[len(names) - len(defaults):], defaults))
        default_value = default_by_name["max_recovery_retries"]
        self.assertIsInstance(default_value, ast.Constant)
        self.assertEqual(default_value.value, 3)

    def test_method_increments_retries(self):
        has_increment = any(
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "recovery_retries"
            and isinstance(node.op, ast.Add)
            and isinstance(node.value, ast.Constant)
            and node.value.value == 1
            for node in ast.walk(self.method_node)
        )
        self.assertTrue(has_increment)

    def test_method_stops_when_retry_budget_exceeded(self):
        has_retry_guard = any(
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "recovery_retries"
            and any(isinstance(op, ast.GtE) for op in node.ops)
            and any(
                isinstance(comp, ast.Name) and comp.id == "max_recovery_retries"
                for comp in node.comparators
            )
            for node in ast.walk(self.method_node)
        )
        self.assertTrue(has_retry_guard)

        has_make_sure_in_world_call = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "make_sure_in_world"
            for node in ast.walk(self.method_node)
        )
        self.assertTrue(has_make_sure_in_world_call)

    def test_farm_in_domain_catches_claim_reward_failure(self):
        # 领奖识别失败(ClaimRewardException)必须被 farm_in_domain 捕获，
        # 转成 (False, must_use) 交回恢复循环，而不是冒泡到框架停任务。
        class_node = next(
            node for node in ast.parse(Path("src/task/DomainTask.py").read_text(encoding="utf-8")).body
            if isinstance(node, ast.ClassDef) and node.name == "DomainTask"
        )
        method = next(
            node for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == "farm_in_domain"
        )
        catches_claim = any(
            isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "ClaimRewardException"
            for node in ast.walk(method)
        )
        self.assertTrue(catches_claim, "farm_in_domain must catch ClaimRewardException")

        source = Path("src/task/DomainTask.py").read_text(encoding="utf-8")
        self.assertIn("ClaimRewardException", source)
        self.assertIn("_last_recovery_reason = 'claim'", source)

    def test_recovery_loop_distinguishes_claim_from_death(self):
        source = Path("src/task/DomainTask.py").read_text(encoding="utf-8")
        # 领奖失败退出副本后不在信标点，需重新传送；死亡恢复才跳过传送。
        self.assertIn("realm_entry_at_heal_point = getattr(self, '_last_recovery_reason', 'death') == 'death'", source)

    def test_cannot_find_f_is_recoverable(self):
        """找不到 F 不能让整个副本步骤失败，要走「退本重进」恢复循环。"""
        source = Path("src/task/DomainTask.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        class_node = next(n for n in module.body
                          if isinstance(n, ast.ClassDef) and n.name == "DomainTask")
        method = next(n for n in class_node.body
                      if isinstance(n, ast.FunctionDef) and n.name == "farm_in_domain")

        # walk_until_f 抛 CannotFindException 必须被捕获
        catches_no_f = any(
            isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "CannotFindException"
            for node in ast.walk(method)
        )
        self.assertTrue(catches_no_f, "farm_in_domain must catch CannotFindException")
        # 恢复标记为 no_f，且退回大世界后交回恢复循环
        self.assertIn("_last_recovery_reason = 'no_f'", source)
        self.assertIn("cannot find F", source)
        self.assertIn("from Mon.task.exceptions import CannotFindException", source)

    def test_loop_unpacks_must_use_from_farm_in_domain(self):
        has_unpack = any(
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple)
            and len(node.targets[0].elts) == 2
            and {elt.id for elt in node.targets[0].elts if isinstance(elt, ast.Name)} == {"finished", "must_use"}
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "farm_in_domain"
            for node in ast.walk(self.method_node)
        )
        self.assertTrue(has_unpack)


if __name__ == "__main__":
    unittest.main()
