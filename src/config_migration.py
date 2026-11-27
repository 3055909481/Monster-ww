"""一次性配置迁移：把旧版程序目录的 configs 统一到 %APPDATA%/Monster-ww/configs。

旧版本把配置写在程序目录的 ``configs/`` 下，新版本统一改用 ``%APPDATA%``，
以便整包更新不丢账号 / 计划 / 开关。首次升级时补入旧配置，之后不再触碰。

迁移规则（用户确认「以 APPDATA 为准」）：
  * APPDATA 已存在的配置一律不动——绝不覆盖用户在当前版本里的设置；
  * 只补 APPDATA 缺失的文件；复制进来的旧文件若含废弃字段，框架加载时会
    用任务 default_config 经 verify_config 清理，不会残留没用的旧配置；
  * 旧版本已删除任务残留的 ``*Task.json`` 移到 ``obsolete_backup/``，
    可恢复，不直接删除。

一次性（marker 控制），并保护账号 / 自定义工作流 / 日常一条龙等用户数据。
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MARKER = '.migration_done'


def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=4)
        return True
    except OSError as error:
        logger.warning('write config failed %s: %s', path, error)
        return False


def migrate_legacy_configs(source_dir, target_dir):
    """把旧目录里「APPDATA 尚不存在」的配置复制过来，返回写入的文件数。

    用户选择「以 APPDATA 为准」：目标已存在的配置一律不动，绝不覆盖用户在
    当前版本里的设置（含账号、工作流、日常一条龙与开关状态）。只补目标缺失
    的文件；复制进来的旧文件若带废弃字段，框架加载时会用任务 default_config
    经 verify_config 自动清理，不会残留。

    这是一次性迁移：marker 存在后不再执行。
    """
    if not source_dir or not target_dir:
        return 0
    source = Path(source_dir)
    target = Path(target_dir)
    if not source.is_dir() or not target.is_dir():
        return 0
    if os.path.normcase(os.path.normpath(str(source))) == os.path.normcase(os.path.normpath(str(target))):
        return 0
    marker = target / MARKER
    if marker.exists():
        return 0

    migrated_files = 0
    for path in sorted(source.glob('*.json')):
        target_path = target / path.name
        if target_path.exists():
            continue  # APPDATA 已有：以用户现有配置为准，不覆盖
        source_data = read_json(path)
        if source_data is None:
            continue
        if write_json(target_path, source_data):
            migrated_files += 1
            logger.info('migrated config %s (was missing in APPDATA)', path.name)

    try:
        marker.write_text('migrated\n', encoding='utf-8')
    except OSError as error:
        logger.warning('write migration marker failed: %s', error)
    return migrated_files


def active_task_names():
    """当前版本注册的任务类名（来自 config.py 的 onetime/trigger 清单）。"""
    names = set()
    try:
        import config as project_config
        cfg = getattr(project_config, 'config', {}) or {}
        for key in ('onetime_tasks', 'trigger_tasks'):
            for entry in cfg.get(key, []) or []:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    names.add(entry[1])
    except Exception as error:
        logger.warning('read active task list failed: %s', error)
    return names


# 用户数据 / 框架配置：即使名字以 Task.json 结尾也绝不能清理。
# 账号配置、自定义工作流、日常一条龙、团队配置等都属于用户资产。
PROTECTED_CONFIGS = frozenset({
    'MultiAccountConfig', 'MultiAccountConfigTask',
    'CustomFlows', 'CustomFlowTask',
    'RoutinePlans', 'RoutineTask',
    'Character Config', 'Basic Options', 'Game Hotkey',
    'Monthly Card Config',
})


def prune_obsolete_task_configs(target_dir, active_names=None):
    """把目标目录里「当前版本已不注册的任务」残留配置移到备份目录。

    只处理 ``*Task.json`` 且文件名不在注册清单里的配置——这类文件是旧版本
    删掉任务后残留的垃圾。三重保护：
      1. PROTECTED_CONFIGS 里的用户数据永不清理；
      2. 拿不到有效任务清单时整体跳过；
      3. 不直接删除，移动到 ``obsolete_backup/`` 供用户恢复。

    框架自身配置（devices/main_window/...）和用户数据（CustomFlows/
    RoutinePlans/...）不带 Task 后缀，本来就匹配不到。返回清理数量。
    """
    if not target_dir:
        return 0
    if active_names is None:
        active_names = active_task_names()
    if not active_names:
        logger.warning('no active task list; skip obsolete config cleanup')
        return 0
    target = Path(target_dir)
    if not target.is_dir():
        return 0
    removed = 0
    for path in sorted(target.glob('*Task.json')):
        name = path.stem
        if name in active_names or name in PROTECTED_CONFIGS:
            continue
        try:
            backup = target / 'obsolete_backup'
            backup.mkdir(exist_ok=True)
            path.rename(backup / path.name)
            removed += 1
            logger.info('moved obsolete task config %s to backup', path.name)
        except OSError as error:
            logger.warning('move obsolete config %s failed: %s', path.name, error)
    return removed


def install():
    """在项目配置目录确定后调用；自动探测旧目录并执行一次性迁移。

    迁移完成后清理目标目录里旧版本遗留、当前版本已不注册的任务配置。清理
    只在首次迁移（marker 不存在）时做，避免每次启动都扫描删除。
    """
    try:
        import config as project_config
        target = getattr(project_config, 'CONFIG_FOLDER', None)
    except Exception:
        target = None
    if not target:
        return 0
    first_run = not (Path(target) / MARKER).exists()
    migrated = migrate_legacy_configs(os.path.join(os.getcwd(), 'configs'), target)
    if first_run:
        prune_obsolete_task_configs(target)
    return migrated
