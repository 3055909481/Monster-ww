"""定时任务启动参数：计划配置/账号的读写与命令行编码。"""

import sys
import unittest
from unittest.mock import patch

from src.schedule_runtime import (
    ACCOUNT_ARG, PLAN_ARG, SERVER_ARG, build_task_args, scheduled_account,
    scheduled_plan, scheduled_server,
)


class TestScheduleRuntime(unittest.TestCase):
    def test_read_space_separated_args(self):
        argv = ['main.py', '-t', 'src.task.RoutineTask',
                PLAN_ARG, '国际服', ACCOUNT_ARG, 'user@example.com']
        with patch.object(sys, 'argv', argv):
            self.assertEqual(scheduled_plan(), '国际服')
            self.assertEqual(scheduled_account(), 'user@example.com')

    def test_read_equals_args(self):
        argv = ['main.py', '-t', '1', f'{PLAN_ARG}=默认', f'{ACCOUNT_ARG}=abc']
        with patch.object(sys, 'argv', argv):
            self.assertEqual(scheduled_plan(), '默认')
            self.assertEqual(scheduled_account(), 'abc')

    def test_missing_args_return_none(self):
        with patch.object(sys, 'argv', ['main.py', '-t', '1']):
            self.assertIsNone(scheduled_plan())
            self.assertIsNone(scheduled_account())

    def test_build_task_args_quotes_values(self):
        encoded = build_task_args('国际服 计划', 'user name@example.com')
        self.assertEqual(
            encoded,
            f'{PLAN_ARG} "国际服 计划" {ACCOUNT_ARG} "user name@example.com"')

    def test_build_task_args_skips_empty(self):
        self.assertEqual(build_task_args(None, None), '')
        self.assertEqual(build_task_args('默认', ''), f'{PLAN_ARG} "默认"')

    def test_roundtrip(self):
        encoded = build_task_args('默认', 'abc')
        argv = ['main.py', '-t', '1'] + encoded.split(' ')
        with patch.object(sys, 'argv', argv):
            self.assertEqual(scheduled_plan(), '默认')
            self.assertEqual(scheduled_account(), 'abc')

    def test_server_arg_read_and_encoding(self):
        argv = ['main.py', '-t', '1', SERVER_ARG, '国际服']
        with patch.object(sys, 'argv', argv):
            self.assertEqual(scheduled_server(), '国际服')
        encoded = build_task_args(None, None, '国际服')
        self.assertEqual(encoded, f'{SERVER_ARG} "国际服"')

    def test_build_task_args_with_all_three(self):
        encoded = build_task_args('默认', 'abc', '国服')
        self.assertEqual(
            encoded,
            f'{PLAN_ARG} "默认" {ACCOUNT_ARG} "abc" {SERVER_ARG} "国服"')


if __name__ == '__main__':
    unittest.main()
