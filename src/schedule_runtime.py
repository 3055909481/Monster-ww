"""定时任务启动时携带的运行参数（计划配置 / 执行账号）。

Windows 计划任务以 ``main.py -t <task> --routine-plan <计划名> --schedule-account <账号>``
启动。框架的 parse_arguments 用 parse_known_args，会忽略这些自定义参数但把它们
留在 sys.argv 里，所以这里直接从 sys.argv 读取，避免改动框架。

这些参数只对当次进程生效；不写回任何用户配置。
"""

import sys
from typing import Optional

# 命令行开关名（同时作为 Windows 计划任务参数写入）
PLAN_ARG = '--routine-plan'
ACCOUNT_ARG = '--schedule-account'
SERVER_ARG = '--schedule-server'

# 进程级"当前登录账号"。CLI 的 --schedule-account 只反映启动参数, 多账号
# 流程里切号后它不会变; 流程内嵌任务还会临时屏蔽它(_no_scheduled_account)。
# 登录成功后写入这里, 让体力账本等按账号隔离的逻辑拿到真实账号。
_current_account = None


def set_current_account(account):
    """Record the account that is actually logged in (best effort)."""
    global _current_account
    _current_account = str(account or '').strip() or None


def current_account():
    """The account currently logged in, independent of CLI/scheduling args."""
    return _current_account


def _clean(value: Optional[str]) -> Optional[str]:
    """去掉 shell/模拟 split 后可能残留的包裹双引号。"""
    if value is None:
        return None
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return value or None


def _read_arg(name: str) -> Optional[str]:
    """从 sys.argv 读取 ``--key value`` 或 ``--key=value``，返回首值。"""
    argv = list(getattr(sys, 'argv', []) or [])
    for index, token in enumerate(argv):
        if token == name:
            if index + 1 < len(argv) and not argv[index + 1].startswith('-'):
                return _clean(argv[index + 1])
            return None
        if token.startswith(name + '='):
            return _clean(token.split('=', 1)[1])
    return None


def scheduled_plan() -> Optional[str]:
    """本次定时任务指定的一条龙计划名；未指定返回 None。"""
    return _read_arg(PLAN_ARG)


def scheduled_account() -> Optional[str]:
    """本次定时任务指定的执行账号 key；未指定返回 None。"""
    return _read_arg(ACCOUNT_ARG)


def scheduled_server() -> Optional[str]:
    """本次定时任务指定的客户端服务器（国服/国际服）；未指定返回 None。"""
    return _read_arg(SERVER_ARG)


def build_task_args(plan: Optional[str] = None, account: Optional[str] = None,
                    server: Optional[str] = None) -> str:
    """构造追加到 ``-t <task>`` 之后的命令行参数片段。

    账号/计划名可能含空格，统一用双引号包裹；内部双引号去除以防注入。
    """
    parts = []
    for name, value in ((PLAN_ARG, plan), (ACCOUNT_ARG, account), (SERVER_ARG, server)):
        if value:
            safe = str(value).replace('"', '').strip()
            if safe:
                parts.append(f'{name} "{safe}"')
    return ' '.join(parts)
