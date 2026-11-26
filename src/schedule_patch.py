"""项目层补丁：给框架的「创建计划任务」弹窗增加“计划配置 / 执行账号”。

框架 Mon-script 是 pip 包，不随本项目打包，因此不直接改框架源码；这里在
main.py 启动早期 monkeypatch 三个点：

1. CreateScheduleTaskDialog.__init__ —— 追加两个下拉框（选中「日常一条龙」时可用）
2. CreateScheduleTaskDialog.on_create  —— 把下拉框选择暂存在实例上
3. ScheduleTaskTab.on_task_created     —— 把选择传给 create_task
4. WindowsScheduleManager.create_task  —— 把选择编码进命令行参数

运行时由 src.schedule_runtime 从 sys.argv 读回，RoutineTask 据此切计划/切账号。
任何一步出错都静默降级为框架原行为，不影响原本的定时任务功能。
"""

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False

# on_create -> on_task_created 之间传递本次选择（框架 signal 不带这些值）
_LAST_EXTRAS = {'plan': '', 'account': '', 'server': ''}


def _accounts():
    try:
        from src.task.account.MultiAccountConfigStore import MultiAccountConfigStore
        from Mon import og
        folder = og.config.get('config_folder', 'configs') if og.config else 'configs'
        return [a.get('account_key') for a in MultiAccountConfigStore(folder).load()
                if a.get('account_key')]
    except Exception:
        return []


def _plans():
    try:
        from src.task.routine.RoutinePlans import RoutinePlanStore
        from Mon import og
        folder = og.config.get('config_folder', 'configs') if og.config else 'configs'
        return list(RoutinePlanStore(folder).load())
    except Exception:
        return []


def _flows():
    try:
        from src.task.routine.FlowSteps import FlowStepStore
        from Mon import og
        folder = og.config.get('config_folder', 'configs') if og.config else 'configs'
        return list(FlowStepStore(folder).load())
    except Exception:
        return []


def _is_routine_task(task):
    return type(task).__name__ in ('RoutineTask', 'CustomFlowTask')


def _tr(text):
    try:
        from Mon import og
        return og.app.tr(text) if og.app else text
    except Exception:
        return text


def install():
    """安装所有补丁；可重复调用，失败不影响主流程。"""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    try:
        _patch_create_dialog()
        _patch_task_created()
        _patch_create_task()
    except Exception:
        logger.exception('schedule_patch install failed; falling back to stock behavior')


def _patch_create_dialog():
    from PySide6.QtWidgets import QLabel
    from Mon.ui.qt.kit.inputs import ComboBox
    from Mon.ui.qt.tasks import ScheduleTaskTab as mod

    original_init = mod.CreateScheduleTaskDialog.__init__

    def patched_init(self, parent=None):
        original_init(self, parent)
        grid = self.form.grid
        row = getattr(self.form, '_row', 0)
        try:
            # 计划配置只对「日常一条龙」生效；执行账号与客户端服务器对所有
            # 任务生效（任何任务都可在启动时登录指定账号、选择国服/国际服）。
            self.schedule_plan_label = QLabel(_tr('计划配置'), self.panel)
            self.schedule_plan_combo = ComboBox(parent=self.panel)
            self.schedule_plan_combo.addItems([_tr('不限制（沿用计划默认）')] + _plans())
            grid.addWidget(self.schedule_plan_label, row, 0)
            grid.addWidget(self.schedule_plan_combo, row, 1)
            row += 1

            self.schedule_account_label = QLabel(_tr('执行账号'), self.panel)
            self.schedule_account_combo = ComboBox(parent=self.panel)
            self.schedule_account_combo.addItems([_tr('当前账号')] + _accounts())
            grid.addWidget(self.schedule_account_label, row, 0)
            grid.addWidget(self.schedule_account_combo, row, 1)
            row += 1

            self.schedule_server_label = QLabel(_tr('客户端服务器'), self.panel)
            self.schedule_server_combo = ComboBox(parent=self.panel)
            self.schedule_server_combo.addItems([_tr('跟随全局'), '国服', '国际服'])
            grid.addWidget(self.schedule_server_label, row, 0)
            grid.addWidget(self.schedule_server_combo, row, 1)
            row += 1
            self.form._row = row

            def _sync_extras_visibility(index=None):
                selected = self.tasks[index] if (index is not None
                                                 and 0 <= index < len(self.tasks)) else None
                # Keep using the helper as the single source of truth: tests and
                # callers patch _is_routine_task to recognise stub task objects.
                is_routine = _is_routine_task(selected) if selected is not None else False
                kind = type(selected).__name__ if selected is not None else ''
                # 计划/流程配置仅一条龙与自定义任务流可见；账号、服务器对所有任务可见。
                for widget in (self.schedule_plan_label, self.schedule_plan_combo):
                    widget.setVisible(is_routine)
                if is_routine:
                    # 一条龙显示「计划配置」，任务流显示「任务流」；复用同一
                    # --routine-plan 参数通道，运行时各自按名字读取。
                    options = _flows() if kind == 'CustomFlowTask' else _plans()
                    label = _tr('任务流') if kind == 'CustomFlowTask' else _tr('计划配置')
                    current = self.schedule_plan_combo.currentText()
                    self.schedule_plan_label.setText(label)
                    self.schedule_plan_combo.blockSignals(True)
                    self.schedule_plan_combo.clear()
                    self.schedule_plan_combo.addItems(
                        [_tr('不限制（沿用计划默认）')] + options)
                    if current in options:
                        self.schedule_plan_combo.setCurrentText(current)
                    self.schedule_plan_combo.blockSignals(False)
                else:
                    # 切走时清空配置，防止残留值被写进定时任务参数
                    self.schedule_plan_combo.setCurrentIndex(0)
                try:
                    # 与框架 _on_trigger_type_changed 保持一致：只在窗口可见时
                    # 用 _fit() 重新居中面板。dialog 本身铺满父窗口且没有 layout，
                    # 直接 adjustSize() 会把它收缩到极小的 sizeHint，令 panel 错位、
                    # 内容看起来被挤压（这正是动态显示隐藏引入拥挤感的原因）。
                    if self.form.dialog.isVisible():
                        self.form.dialog._fit()
                except Exception:
                    pass

            self._sync_schedule_extras = _sync_extras_visibility
            try:
                self.task_combo.currentIndexChanged.connect(_sync_extras_visibility)
                _sync_extras_visibility(self.task_combo.currentIndex())
            except Exception:
                pass
        except Exception:
            logger.exception('failed to add schedule extras to dialog')

    mod.CreateScheduleTaskDialog.__init__ = patched_init

    original_create = mod.CreateScheduleTaskDialog.on_create

    def patched_on_create(self):
        try:
            index = self.task_combo.currentIndex()
            is_routine = 0 <= index < len(self.tasks) and _is_routine_task(self.tasks[index])
            # 计划配置仅一条龙；账号/服务器对所有任务都读取。
            _LAST_EXTRAS['plan'] = (self.schedule_plan_combo.currentText()
                                    if is_routine and hasattr(self, 'schedule_plan_combo') else '')
            _LAST_EXTRAS['account'] = (self.schedule_account_combo.currentText()
                                       if hasattr(self, 'schedule_account_combo') else '')
            _LAST_EXTRAS['server'] = (self.schedule_server_combo.currentText()
                                      if hasattr(self, 'schedule_server_combo') else '')
        except Exception:
            _LAST_EXTRAS['plan'] = ''
            _LAST_EXTRAS['account'] = ''
            _LAST_EXTRAS['server'] = ''
        return original_create(self)

    mod.CreateScheduleTaskDialog.on_create = patched_on_create


def _patch_task_created():
    from Mon.ui.qt.tasks import ScheduleTaskTab as mod

    original = mod.ScheduleTaskTab.on_task_created

    def patched(self, name, task_index, trigger_type, timeout_hours, start_hour, start_minute,
                auto_exit, interval_days=0, interval_hours=0):
        # on_create 已把本次选择写入模块变量（signal 不带这两个值）
        plan = _LAST_EXTRAS.get('plan', '')
        account = _LAST_EXTRAS.get('account', '')
        server = _LAST_EXTRAS.get('server', '')
        manager = getattr(self, 'schedule_manager', None)
        if manager is not None:
            manager._next_plan = plan
            manager._next_account = account
            manager._next_server = server
        try:
            return original(self, name, task_index, trigger_type, timeout_hours, start_hour,
                            start_minute, auto_exit, interval_days, interval_hours)
        finally:
            _LAST_EXTRAS['plan'] = ''
            _LAST_EXTRAS['account'] = ''
            _LAST_EXTRAS['server'] = ''

    mod.ScheduleTaskTab.on_task_created = patched


def _patch_create_task():
    import re
    from src.schedule_runtime import build_task_args
    from Mon.util import windows_schedule as mod

    original_generate = mod.WindowsScheduleManager._generate_task_xml

    def patched_generate(self, *args, **kwargs):
        xml = original_generate(self, *args, **kwargs)
        plan = getattr(self, '_next_plan', '') or ''
        account = getattr(self, '_next_account', '') or ''
        server = getattr(self, '_next_server', '') or ''
        extra = build_task_args(
            None if plan.startswith(('不限制', '当前账号')) or not plan else plan,
            None if account.startswith(('不限制', '当前账号')) or not account else account,
            None if server.startswith(('跟随全局', '不限制')) or server not in ('国服', '国际服') else server,
        )
        if not extra:
            return xml
        # 把自定义参数追加进 <Arguments>main.py -t ...</Arguments>
        def append(match):
            inner = match.group(1).rstrip()
            return f'<Arguments>{inner} {extra}</Arguments>'

        try:
            xml = re.sub(r'<Arguments>(.*?)</Arguments>', append, xml, count=1, flags=re.S)
        except Exception:
            logger.exception('failed to append schedule args to XML')
        return xml

    mod.WindowsScheduleManager._generate_task_xml = patched_generate
