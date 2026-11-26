"""Custom task-flow step registry, persistence and pure adapters.

A flow is an ordered list of steps that drives the whole client lifecycle, e.g.
start CN client -> login account A -> run routine -> switch to account B -> run
routine -> exit CN client -> start global client -> ...

    [{"id": "start_game", "enabled": true,
      "params": {"服务器": "国服"}}, ...]

Everything in this module is plain Python on purpose: the editor, the runtime
task and the unit tests all share it, and it must stay importable without Mon,
PySide6 or any compiled/native module.
"""

import json
import os
import tempfile
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FLOW_NAME = '默认流程'
FLOW_SELECTOR = 'Custom Flow'
EDIT_FLOWS = 'Configure Custom Flows'

# ------------------------------------------------------------------ servers
SERVER_CN = '国服'
SERVER_GLOBAL = '国际服'
SERVER_FOLLOW = '跟随全局'
SERVER_OPTIONS = (SERVER_FOLLOW, SERVER_CN, SERVER_GLOBAL)

# ------------------------------------------------------------------ accounts
ACCOUNT_SOURCE_SELECT = '从已配置账号选择'
ACCOUNT_SOURCE_MANUAL = '手动输入账号'
ACCOUNT_SOURCES = (ACCOUNT_SOURCE_SELECT, ACCOUNT_SOURCE_MANUAL)

# --------------------------------------------------------------------- tasks
# Display name -> (module, class). Keep this list in sync with config.py's
# onetime_tasks; it is intentionally static so the store stays pure Python.
TASK_REFERENCES = {
    '日常一条龙': ('src.task.RoutineTask', 'RoutineTask'),
    '领取活跃度与邮件': ('src.task.DailyTask', 'DailyTask'),
    '梦魇巢穴': ('src.task.NightmareNestTask', 'NightmareNestTask'),
    '梦幻游园（周常乐园）': ('src.task.GardenTask', 'GardenTask'),
    '无音清剿': ('src.task.TacetTask', 'TacetTask'),
    '凝素领域': ('src.task.ForgeryTask', 'ForgeryTask'),
    '模拟领域': ('src.task.SimulationTask', 'SimulationTask'),
    '培养目标自动补齐': ('src.task.TrainingGuideTask', 'TrainingGuideTask'),
    '刷取声骸': ('src.task.FarmEchoTask', 'FarmEchoTask'),
    '周度游历奖励': ('src.task.DailyTask', 'DailyTask'),
}

ROUTINE_TASK_NAME = '日常一条龙'
ROUTINE_PLAN_KEY = '计划'


@dataclass(frozen=True)
class FlowEntry:
    """One selectable flow step.

    ``default_config`` / ``config_description`` / ``config_type`` follow the
    same ConfigForm contract used by RoutineEntry, so the editor can reuse the
    exact same form renderer.
    """

    task_id: str
    name: str
    default_config: dict = field(default_factory=dict)
    config_description: dict = field(default_factory=dict)
    config_type: dict = field(default_factory=dict)
    description: str = ''
    icon: str = 'tasks'


ENTRIES = [
    FlowEntry(
        'start_game', '启动游戏',
        {'服务器': SERVER_CN},
        {'服务器': '要启动的客户端服务器。若当前运行的是另一个服务器，会先关闭再启动。'},
        {'服务器': {'type': 'drop_down', 'options': [SERVER_CN, SERVER_GLOBAL]}},
        description='按指定服务器启动鸣潮客户端，并等待进入可登录状态。',
        icon='play',
    ),
    FlowEntry(
        'login', '登录账号',
        {'账号来源': ACCOUNT_SOURCE_SELECT, '选择账号': '', '手动账号': '',
         '服务器': SERVER_FOLLOW},
        {'账号来源': '从多账号配置里选，或手动填写账号 key。',
         '选择账号': '从「多账号配置」已添加的账号中选择。',
         '手动账号': '账号 key：手机号去掉所有 *，或邮箱原文（如 abc@outlook.com）。',
         '服务器': '登录前要确认的服务器；一般跟随「启动游戏」步骤即可。'},
        {'账号来源': {'type': 'drop_down', 'options': list(ACCOUNT_SOURCES)},
         '选择账号': {'type': 'line_edit'},
         '手动账号': {'type': 'line_edit'},
         '服务器': {'type': 'drop_down', 'options': list(SERVER_OPTIONS)}},
        description='在游戏登录页选中账号并登录，进入大世界。',
        icon='link',
    ),
    FlowEntry(
        'switch_account', '切换账号',
        {'账号来源': ACCOUNT_SOURCE_SELECT, '选择账号': '', '手动账号': '',
         '服务器': SERVER_FOLLOW},
        {'账号来源': '从多账号配置里选，或手动填写账号 key。',
         '选择账号': '从「多账号配置」已添加的账号中选择。',
         '手动账号': '账号 key：手机号去掉所有 *，或邮箱原文（如 abc@outlook.com）。',
         '服务器': '登录前要确认的服务器；一般跟随「启动游戏」步骤即可。'},
        {'账号来源': {'type': 'drop_down', 'options': list(ACCOUNT_SOURCES)},
         '选择账号': {'type': 'line_edit'},
         '手动账号': {'type': 'line_edit'},
         '服务器': {'type': 'drop_down', 'options': list(SERVER_OPTIONS)}},
        description='从游戏内退回登录页，切换到指定账号并登录。',
        icon='users',
    ),
    FlowEntry(
        'run_task', '执行任务',
        {'任务': ROUTINE_TASK_NAME, ROUTINE_PLAN_KEY: ''},
        {'任务': '选择要执行的已有任务；日常一条龙可再指定一个计划。',
         ROUTINE_PLAN_KEY: '仅对「日常一条龙」有效；留空使用该任务当前设定的计划。'},
        {'任务': {'type': 'drop_down', 'options': list(TASK_REFERENCES)},
         ROUTINE_PLAN_KEY: {'type': 'line_edit'}},
        description='在已登录账号上执行一次所选任务，失败会被隔离，后续步骤照常运行。',
        icon='tasks',
    ),
    FlowEntry(
        'exit_game', '退出游戏',
        {}, {}, {},
        description='关闭当前鸣潮客户端，为启动另一个服务器做准备。',
        icon='power',
    ),
]

ENTRIES_BY_ID = {entry.task_id: entry for entry in ENTRIES}


def describe_params(entry, params):
    """Readable ``key value`` pairs for a step's current settings."""
    pairs = []
    values = params if isinstance(params, dict) else {}
    for key, default in entry.default_config.items():
        value = values.get(key, default)
        if value is None or value == '' or value == SERVER_FOLLOW:
            continue
        pairs.append(f'{key} {value}')
    return pairs


def resolve_flow_account(params):
    """Return the account key a login/switch step should use.

    Manual input wins when selected; otherwise the picked account is used.
    A legacy plain ``账号`` key is accepted for old/imported flows.
    """
    values = params if isinstance(params, dict) else {}
    if values.get('账号来源', ACCOUNT_SOURCE_SELECT) == ACCOUNT_SOURCE_MANUAL:
        return str(values.get('手动账号', '') or '').strip()
    picked = values.get('选择账号', '')
    if picked:
        return str(picked).strip()
    # Backward compatibility with hand-written flows using a single key.
    return str(values.get('账号', '') or '').strip()


def task_reference(task_name):
    """Resolve a display name from the step registry to (module, class)."""
    return TASK_REFERENCES.get(str(task_name).strip())


def entry_config_type(entry_id, *, accounts=(), plans=()):
    """Return a ConfigForm config_type with dynamic drop-down options filled.

    ``accounts`` are account keys from MultiAccountConfig.json and ``plans``
    are routine plan names from RoutinePlans.json. Both are optional so the
    base registry stays dependency-free.
    """
    entry = ENTRIES_BY_ID.get(entry_id)
    if entry is None:
        return {}
    config_type = deepcopy(entry.config_type)
    if entry_id in ('login', 'switch_account'):
        picked = config_type.get('选择账号', {})
        if isinstance(picked, dict):
            picked['options'] = list(accounts)
            if accounts:
                picked['type'] = 'drop_down'
            else:
                # No configured accounts yet: keep a free-text box so manual
                # input still works.
                picked['type'] = 'line_edit'
    if entry_id == 'run_task':
        plan = config_type.get(ROUTINE_PLAN_KEY, {})
        if isinstance(plan, dict):
            plan['options'] = list(plans)
            if plans:
                plan['type'] = 'drop_down'
            else:
                plan['type'] = 'line_edit'
    return config_type


def new_item(task_id):
    """Create a fresh step with a unique uid so the same step can repeat."""
    entry = ENTRIES_BY_ID.get(task_id)
    if entry is None:
        raise ValueError(f'Unknown flow step: {task_id}')
    return {
        'id': task_id,
        'uid': uuid.uuid4().hex,
        'enabled': True,
        'params': deepcopy(entry.default_config),
    }


def normalize_items(items):
    """Validate steps, fill params, assign uids. Keeps duplicates on purpose."""
    raw = items if isinstance(items, list) else []
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_id = item.get('id')
        entry = ENTRIES_BY_ID.get(task_id)
        if entry is None:
            continue
        params = deepcopy(entry.default_config)
        if isinstance(item.get('params'), dict):
            params.update({key: value for key, value in item['params'].items()
                           if key in entry.default_config})
        uid = item.get('uid')
        if not isinstance(uid, str) or not uid:
            uid = uuid.uuid4().hex
        normalized.append({'id': task_id, 'uid': uid,
                           'enabled': bool(item.get('enabled', True)),
                           'params': params})
    return normalized


def default_items():
    """A starter flow covering one CN and one global account."""
    flow = [
        ('start_game', {'服务器': SERVER_CN}),
        ('login', {'账号来源': ACCOUNT_SOURCE_SELECT, '选择账号': '', '手动账号': '',
                   '服务器': SERVER_FOLLOW}),
        ('run_task', {'任务': ROUTINE_TASK_NAME, ROUTINE_PLAN_KEY: ''}),
        ('exit_game', {}),
        ('start_game', {'服务器': SERVER_GLOBAL}),
        ('login', {'账号来源': ACCOUNT_SOURCE_SELECT, '选择账号': '', '手动账号': '',
                   '服务器': SERVER_FOLLOW}),
        ('run_task', {'任务': ROUTINE_TASK_NAME, ROUTINE_PLAN_KEY: ''}),
        ('exit_game', {}),
    ]
    result = []
    for task_id, params in flow:
        item = new_item(task_id)
        item['params'].update(params)
        result.append(item)
    return result


def normalize_flows(flows):
    if not isinstance(flows, dict) or not flows:
        return {DEFAULT_FLOW_NAME: default_items()}
    normalized = {}
    for name, items in flows.items():
        if isinstance(name, str) and name.strip():
            normalized[name.strip()] = normalize_items(items)
    return normalized or {DEFAULT_FLOW_NAME: default_items()}


class FlowStepStore:
    """Atomic JSON store, independent of the framework Config merge rules."""

    def __init__(self, folder='configs'):
        self.path = Path(folder) / 'CustomFlows.json'

    def load(self):
        if not self.path.exists():
            return {DEFAULT_FLOW_NAME: default_items()}
        try:
            with self.path.open(encoding='utf-8') as stream:
                flows = json.load(stream)
        except (OSError, ValueError):
            return {DEFAULT_FLOW_NAME: default_items()}
        return normalize_flows(flows)

    def save(self, flows):
        normalized = normalize_flows(flows)
        content = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent,
                                             prefix='.CustomFlows-', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return normalized


class FlowItemConfig(dict):
    """ConfigForm-compatible view over one flow step's params."""

    def __init__(self, values, defaults, on_change=None):
        self.default = deepcopy(defaults)
        self.on_change = on_change
        super().__init__(deepcopy(defaults))
        if isinstance(values, dict):
            self.update(deepcopy(values))

    def get_default(self, key):
        return deepcopy(self.default.get(key))

    def has_user_config(self):
        return bool(self.default)

    def reset_to_default(self):
        dict.clear(self)
        dict.update(deepcopy(self.default))
        self._notify()

    def save_file(self):
        pass

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._notify()

    def _notify(self):
        if self.on_change is not None:
            self.on_change(dict(self))
