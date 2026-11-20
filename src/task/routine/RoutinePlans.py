"""Routine plan registry, persistence and config adapters.

A plan is an ordered list of items::

    [{"id": "forgery", "enabled": true,
      "params": {"地区": "瑾珑·梦州", "武器类型": "长刃", "次数": 2}}, ...]

Plans live in ``configs/RoutinePlans.json`` so each multi-account entry can
select its own plan name and get an independent stamina workflow.
"""

import json
import os
import tempfile
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.task.routine import RoutineCatalog as catalog

DEFAULT_PLAN_NAME = '默认'
PLAN_SELECTOR = 'Routine Plan'
EDIT_PLANS = 'Configure Routine Plans'

WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
ANY_DAY = '不限制'
RUN_DAYS_KEY = '运行日'
RUN_DAY_OPTIONS = WEEKDAYS + [ANY_DAY]


def is_scheduled_today(params, today=None):
    """Whether a weekly step is allowed to run on ``today``.

    ``运行日`` is a single choice: one of 周一..周日, or ``不限制`` (run daily).
    A legacy multi-select list is still accepted: an empty / all-week list means
    unrestricted, otherwise the day must be in it.
    """
    value = params.get(RUN_DAYS_KEY) if isinstance(params, dict) else None
    if value is None or value == '' or value == ANY_DAY:
        return True
    if isinstance(value, list):
        if not value or len(value) >= len(WEEKDAYS):
            return True
        days = value
    else:
        days = [value] if value in WEEKDAYS else []
    if not days:
        return True
    if today is None:
        today = date.today()
    return WEEKDAYS[today.weekday()] in days


def _normalize_run_days(value):
    """Coerce a stored 运行日 value into a single valid option (drop-down)."""
    if isinstance(value, str):
        return value if value in RUN_DAY_OPTIONS else ANY_DAY
    if isinstance(value, list):
        valid = [day for day in value if day in WEEKDAYS]
        if not valid or len(valid) >= len(WEEKDAYS):
            return ANY_DAY
        return valid[0]
    return ANY_DAY


def _run_days_schema():
    return {RUN_DAYS_KEY: ANY_DAY}, {
        RUN_DAYS_KEY: '只在所选星期执行；选「不限制」则每天都跑。',
    }, {
        RUN_DAYS_KEY: {'type': 'drop_down', 'options': list(RUN_DAY_OPTIONS)},
    }


@dataclass(frozen=True)
class RoutineEntry:
    task_id: str
    name: str
    group: str
    default_config: dict
    config_description: dict
    config_type: dict


def _count_schema():
    return {
        '次数': 0,
    }, {
        '次数': '刷取次数；0 表示刷到体力耗尽（战歌重奏请填 1~3）。',
    }, {
        '次数': {'min': 0, 'max': 99},
    }


def _build_entries():
    count_default, count_desc, count_type = _count_schema()
    days_default, days_desc, days_type = _run_days_schema()
    entries = [
        RoutineEntry('claim_daily', '领取活跃度奖励 / 邮件', '奖励',
                     {}, {}, {}),
        RoutineEntry('battle_pass', '领取先约电台经验与等级奖励', '奖励',
                     {}, {}, {}),
        RoutineEntry('weekly_garden', '周度游历奖励（梦幻游园）', '奖励',
                     deepcopy(days_default), deepcopy(days_desc), deepcopy(days_type)),
        RoutineEntry('simulation', '模拟领域', '清体力',
                     {'地区': catalog.simulation_regions()[0],
                      '材料': 'Shell Credit', **count_default},
                     {'地区': '按地区选择；一次 40 体力。',
                      '材料': '共鸣者经验 / 武器经验 / 贝币；一次 40 体力。', **count_desc},
                     {'地区': {'type': 'drop_down', 'options': catalog.simulation_regions()},
                      '材料': {'type': 'drop_down', 'options': catalog.simulation_options()},
                      **count_type}),
        RoutineEntry('forgery', '凝素领域', '清体力',
                     {'地区': catalog.forgery_region_names()[0],
                      '武器类型': catalog.forgery_weapon_types()[0],
                      **count_default},
                     {'地区': '按地区选择；一次 40 体力。',
                      '武器类型': '长刃 / 迅刃 / 佩枪 / 臂铠 / 音感仪。',
                      **count_desc},
                     {'地区': {'type': 'drop_down', 'options': catalog.forgery_region_names()},
                      '武器类型': {'type': 'drop_down', 'options': catalog.forgery_weapon_types()},
                      **count_type}),
        RoutineEntry('tacet', '无音清剿', '清体力',
                     {'无音区': catalog.tacet_zones()[0], **count_default},
                     {'无音区': '从 F2 手册从上往下选择；一次 60 体力。', **count_desc},
                     {'无音区': {'type': 'drop_down', 'options': catalog.tacet_zones()},
                      **count_type}),
        RoutineEntry('boss_challenge', '讨伐强敌', '清体力',
                     {'BOSS': catalog.boss_challenges()[0], '次数': 1},
                     {'BOSS': '获取共鸣者突破材料；一次 60 体力。',
                      '次数': '重复挑战次数；填 0 表示一直刷。'},
                     {'BOSS': {'type': 'drop_down', 'options': catalog.boss_challenges()},
                      '次数': count_type['次数']}),
        RoutineEntry('weekly_boss', '战歌重奏', '周常',
                     {'周本': catalog.weekly_display_names()[0], '次数': 3,
                      **days_default},
                     {'周本': '按 BOSS 名称展示，实际按副本名称查找；一次 60 体力。',
                      '次数': '每周最多 3 次。', **days_desc},
                     {'周本': {'type': 'drop_down', 'options': catalog.weekly_display_names()},
                      '次数': {'min': 0, 'max': 3}, **days_type}),
        RoutineEntry('training_guide', '培养目标自动补齐', '清体力',
                     {'目标角色': '', '体力预算': 240,
                      '补凝素领域': True, '补讨伐强敌': True, '补战歌重奏': False},
                     {'目标角色': '角色中文名；留空用游戏当前培养目标。',
                      '体力预算': '本次运行总预算；0 表示不限。凝素 40 / 讨伐·战歌 60。',
                      '补凝素领域': '自动补齐武器与技能素材缺口。',
                      '补讨伐强敌': '自动补齐共鸣者突破材料缺口。',
                      '补战歌重奏': '自动补齐技能升级材料缺口。'},
                     {'目标角色': {'type': 'line_edit'},
                      '体力预算': {'min': 0, 'max': 100000},
                      '补凝素领域': {}, '补讨伐强敌': {}, '补战歌重奏': {}}),
    ]
    return entries


ENTRIES = _build_entries()
ENTRIES_BY_ID = {entry.task_id: entry for entry in ENTRIES}


def default_items():
    """A reasonable first-run plan: rewards first, then stamina until empty."""
    return [
        {'id': 'claim_daily', 'enabled': True, 'params': {}},
        {'id': 'battle_pass', 'enabled': True, 'params': {}},
        {'id': 'weekly_garden', 'enabled': True, 'params': {}},
        {'id': 'weekly_boss', 'enabled': False,
         'params': deepcopy(ENTRIES_BY_ID['weekly_boss'].default_config)},
        {'id': 'simulation', 'enabled': False,
         'params': deepcopy(ENTRIES_BY_ID['simulation'].default_config)},
        {'id': 'forgery', 'enabled': True,
         'params': deepcopy(ENTRIES_BY_ID['forgery'].default_config)},
        {'id': 'tacet', 'enabled': False,
         'params': deepcopy(ENTRIES_BY_ID['tacet'].default_config)},
        {'id': 'boss_challenge', 'enabled': False,
         'params': deepcopy(ENTRIES_BY_ID['boss_challenge'].default_config)},
    ]


def new_item(task_id):
    """Create a fresh step with a unique id so the same task can appear twice."""
    entry = ENTRIES_BY_ID.get(task_id)
    if entry is None:
        raise ValueError(f'Unknown routine item: {task_id}')
    return {
        'id': task_id,
        'uid': uuid.uuid4().hex,
        'enabled': True,
        'params': deepcopy(entry.default_config),
    }


def normalize_items(items):
    """Validate items, fill params, assign uids. Keeps duplicates on purpose."""
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
        if RUN_DAYS_KEY in params:
            params[RUN_DAYS_KEY] = _normalize_run_days(params[RUN_DAYS_KEY])
        uid = item.get('uid')
        if not isinstance(uid, str) or not uid:
            uid = uuid.uuid4().hex
        normalized.append({'id': task_id, 'uid': uid,
                           'enabled': bool(item.get('enabled', False)),
                           'params': params})
    return normalized


def normalize_plans(plans):
    if not isinstance(plans, dict) or not plans:
        return {DEFAULT_PLAN_NAME: default_items()}
    normalized = {}
    for name, items in plans.items():
        if isinstance(name, str) and name.strip():
            normalized[name.strip()] = normalize_items(items)
    return normalized or {DEFAULT_PLAN_NAME: default_items()}


class RoutinePlanStore:
    """Atomic JSON store; independent of the framework Config merge rules."""

    def __init__(self, folder='configs'):
        self.path = Path(folder) / 'RoutinePlans.json'

    def load(self):
        if not self.path.exists():
            return {DEFAULT_PLAN_NAME: default_items()}
        with self.path.open(encoding='utf-8') as stream:
            plans = json.load(stream)
        return normalize_plans(plans)

    def save(self, plans):
        normalized = normalize_plans(plans)
        content = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.RoutinePlans-', suffix='.tmp',
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


class PlanItemConfig(dict):
    """ConfigForm-compatible view over one plan item's params."""

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


class RoutinePlanAccountStore:
    """Optional per-plan account binding, kept separate from the plans file.

    Maps plan name -> multi-account ``account_key``. Storing this in its own
    JSON means older RoutinePlans.json files stay byte-for-byte valid and a
    plan with no entry here runs exactly as before (on the current account).
    """

    def __init__(self, folder='configs'):
        self.path = Path(folder) / 'RoutinePlanAccounts.json'

    def load(self):
        if not self.path.exists():
            return {}
        try:
            with self.path.open(encoding='utf-8') as stream:
                document = json.load(stream)
        except (OSError, ValueError):
            return {}
        if not isinstance(document, dict):
            return {}
        return {str(name): str(key) for name, key in document.items()
                if isinstance(name, str) and isinstance(key, str) and key.strip()}

    def get(self, plan_name):
        return self.load().get(str(plan_name))

    def set(self, plan_name, account_key):
        accounts = self.load()
        if account_key is None or not str(account_key).strip():
            accounts.pop(str(plan_name), None)
        else:
            accounts[str(plan_name)] = str(account_key).strip()
        return self.save(accounts)

    def save(self, accounts):
        normalized = {str(name): str(key).strip() for name, key in accounts.items()
                      if isinstance(name, str) and isinstance(key, str) and key.strip()}
        content = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.RoutinePlanAccounts-', suffix='.tmp',
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
