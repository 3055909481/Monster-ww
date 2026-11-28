"""Global monitor for running tasks.

Two separate groups are shown: trigger-style tasks (auto combat, auto pick,
auto login, ... which stay resident) and manually queued one-time tasks.
Every row has its own stop button, and names are shown translated (Chinese).
Registered as a custom tab so the framework itself is untouched.
"""

import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from Mon import og, TriggerTask
from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.widget.CustomTab import CustomTab


def _value_text(value):
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value)
    if isinstance(value, dict):
        return ', '.join(f'{key}={item}' for key, item in value.items())
    return '' if value is None else str(value)


STATUS_LABELS = {
    'pending': '待执行',
    'running': '执行中',
    'success': '成功',
    'failed': '失败',
}


def _flow_progress(task):
    """提取工作流任务的步骤进度，返回 (flow_name, current, steps)。

    无步骤状态时返回 (None, '', [])；UI 据此隐藏进度面板。纯函数，便于测试。
    """
    if task is None:
        return None, '', '', []
    info = getattr(task, 'info', None) or {}
    steps = info.get('步骤状态')
    if not isinstance(steps, (list, tuple)) or not steps:
        return None, '', '', []
    flow_name = info.get('任务流') or ''
    current = info.get('当前步骤') or ''
    account = info.get('当前账号') or ''
    rows = []
    for step in steps:
        if isinstance(step, dict):
            rows.append((
                step.get('index', ''),
                step.get('label', ''),
                step.get('status', 'pending'),
                step.get('error', '') or '',
            ))
    return flow_name, current, account, rows


def _display_name(task):
    """Task name in the user's language (falls back to the raw name)."""
    name = getattr(task, 'name', None) or type(task).__name__
    app = getattr(og, 'app', None)
    if app is not None:
        try:
            translated = app.tr(name)
            if translated:
                return translated
        except Exception:
            pass
    return name


class TaskMonitorTab(CustomTab):
    # Distinct sidebar icon (the default APPLICATION/grid collides with other
    # pages). 'monitor' is a bundled kit icon registered in nav_icons.
    icon = 'monitor'

    def __init__(self):
        self._executor = None
        self._button_tasks = {}  # id(button) -> task, avoids Qt QVariant proxy issues
        self.manual_table = None
        self.trigger_table = None
        self.flow_table = None
        self.flow_header = None
        self.manual_stop_all = None
        self.trigger_stop_all = None
        self.stop_button = None
        super().__init__()
        self.setObjectName('TaskMonitorTab')
        self.tr_name = self.tr('任务监控')
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._refresh)

    @property
    def name(self):
        return self.tr_name

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _make_table(self, parent):
        table = QTableWidget(parent)
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels([
            self.tr('任务'), self.tr('状态'), self.tr('耗时'), self.tr('详情'), self.tr('操作'),
        ])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)

        # Explicit, sensible widths. The old setStretchLastSection(True) blew
        # the last ("操作") column up to hundreds of px and left 详情 mostly
        # empty while squeezing the task name; pin fixed columns and let only
        # the details column absorb spare space.
        header = table.horizontalHeader()
        for column, width in ((0, 180), (1, 72), (2, 64), (4, 72)):
            table.setColumnWidth(column, width)
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        return table

    def _build(self):
        body = QWidget(self.view)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.stop_button = Button(self.tr('停止所有任务'), icon='stop', variant='primary',
                                  parent=body)
        self.stop_button.clicked.connect(self._stop_all)
        header.addWidget(self.stop_button)
        header.addStretch(1)
        layout.addLayout(header)

        # Trigger tasks (auto combat / pick / login ...) stay resident.
        trigger_header = QHBoxLayout()
        trigger_header.addWidget(QLabel(self.tr('触发类任务'), body))
        trigger_header.addStretch(1)
        self.trigger_stop_all = Button(self.tr('停止全部'), parent=body, size='small')
        self.trigger_stop_all.clicked.connect(lambda: self._stop_group('trigger'))
        trigger_header.addWidget(self.trigger_stop_all)
        layout.addLayout(trigger_header)
        self.trigger_table = self._make_table(body)
        layout.addWidget(self.trigger_table, 1)

        # Manually queued one-time tasks.
        manual_header = QHBoxLayout()
        manual_header.addWidget(QLabel(self.tr('手动任务'), body))
        manual_header.addStretch(1)
        self.manual_stop_all = Button(self.tr('停止全部'), parent=body, size='small')
        self.manual_stop_all.clicked.connect(lambda: self._stop_group('manual'))
        manual_header.addWidget(self.manual_stop_all)
        layout.addLayout(manual_header)
        self.manual_table = self._make_table(body)
        layout.addWidget(self.manual_table, 1)

        # 工作流步骤进度面板：只在工作流任务运行时显示。
        self.flow_header = QLabel('', body)
        layout.addWidget(self.flow_header)
        self.flow_table = QTableWidget(body)
        self.flow_table.setColumnCount(4)
        self.flow_table.setHorizontalHeaderLabels([
            self.tr('步骤'), self.tr('名称'), self.tr('状态'), self.tr('详情'),
        ])
        self.flow_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.flow_table.verticalHeader().setVisible(False)
        flow_h = self.flow_table.horizontalHeader()
        for column, width in ((0, 56), (1, 300), (2, 72)):
            self.flow_table.setColumnWidth(column, width)
            flow_h.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        flow_h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.flow_table, 2)

        self.add_widget(body)
        self._refresh()

    @property
    def executor(self):
        return self._executor

    @executor.setter
    def executor(self, value):
        # MainWindow assigns this right after constructing the tab; build the
        # controls on first assignment (showEvent alone is too late for _refresh).
        self._executor = value
        if self.manual_table is None and value is not None:
            self._build()
        elif self.manual_table is not None:
            self._refresh()

    def _visible_tasks(self):
        if self.executor is None:
            return []
        try:
            tasks = list(self.executor.get_all_tasks())
        except Exception:
            tasks = list(getattr(self.executor, 'onetime_tasks', []) or []) + \
                    list(getattr(self.executor, 'trigger_tasks', []) or [])
        # Only visible, still-active tasks. Hidden shells (FStartCombatTask,
        # MultiAccountConfigTask) are config proxies: stopping them does nothing
        # (their `enabled` is a read-only property), so don't show them at all.
        # A stopped task keeps running=True until the executor finishes its
        # control check; dropping it here avoids a stale "已停止" row that
        # lingers until the next task transition.
        current = getattr(self.executor, 'current_task', None)
        return [task for task in tasks
                if getattr(task, 'visible', True)
                and (getattr(task, 'enabled', False) or task is current)]

    @staticmethod
    def _is_trigger(task):
        return isinstance(task, TriggerTask)

    def _fill(self, table, tasks, current, now):
        # Reuse cells/buttons across refreshes: rebuilding every second destroyed
        # the button mid-click (press/release split by the 1s timer) and lost the
        # click. Only add/remove rows when the count actually changes.
        old_count = table.rowCount()
        new_count = len(tasks)
        if old_count > new_count:
            table.setRowCount(new_count)
        elif old_count < new_count:
            table.setRowCount(new_count)

        for row, task in enumerate(tasks):
            running = bool(getattr(task, 'running', False)) and task is current
            if running:
                state = self.tr('运行中')
            elif getattr(task, 'enabled', False):
                state = self.tr('排队中')
            else:
                state = self.tr('已停止')
            started = float(getattr(task, 'start_time', 0) or 0)
            elapsed = '' if not started or not running else f'{int(now - started)}s'
            details = _value_text(getattr(task, 'info', {}) or {})
            values = [_display_name(task), state, elapsed, details]
            for column, value in enumerate(values):
                item = table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, column, item)
                if item.text() != value:
                    item.setText(value)
            button = table.cellWidget(row, 4)
            if button is None:
                button = Button(self.tr('停止'), parent=table, variant='subtle', size='small')
                button.clicked.connect(self._on_row_stop)
                table.setCellWidget(row, 4, button)
            # Keep the mapping in a plain Python dict (setProperty would store
            # the task as a QVariant and may hand back a proxy on Cython builds).
            self._button_tasks[id(button)] = task

    def _refresh(self):
        if self.manual_table is None:
            return
        tasks = self._visible_tasks()
        current = getattr(self.executor, 'current_task', None) if self.executor else None
        trigger = [task for task in tasks if self._is_trigger(task)]
        manual = [task for task in tasks if not self._is_trigger(task)]
        now = time.time()
        self._fill(self.trigger_table, trigger, current, now)
        self._fill(self.manual_table, manual, current, now)
        self._fill_flow(manual, current)
        has_tasks = bool(tasks)
        self.stop_button.setEnabled(has_tasks)
        self.trigger_stop_all.setEnabled(bool(trigger))
        self.manual_stop_all.setEnabled(bool(manual))

    def _fill_flow(self, manual_tasks, current):
        """填充工作流步骤进度；没有工作流任务时隐藏面板。"""
        if self.flow_table is None:
            return
        # 优先显示当前正在执行的工作流，否则显示最近一个带步骤状态的任务。
        flow_task = None
        for task in manual_tasks:
            if task is current and getattr(task, 'info', None) and task.info.get('步骤状态'):
                flow_task = task
                break
        if flow_task is None:
            for task in manual_tasks:
                if getattr(task, 'info', None) and task.info.get('步骤状态'):
                    flow_task = task
                    break

        flow_name, current_label, account, rows = _flow_progress(flow_task)
        visible = bool(rows)
        self.flow_header.setVisible(visible)
        self.flow_table.setVisible(visible)
        if not visible:
            return

        title = self.tr('工作流进度：{name} · {cur}').format(
            name=flow_name, cur=current_label)
        if account:
            title += self.tr(' · 账号 {account}').format(account=account)
        self.flow_header.setText(title)
        self.flow_table.setRowCount(len(rows))
        for row, (index, label, status, error) in enumerate(rows):
            status_text = STATUS_LABELS.get(status, status)
            detail = error if status == 'failed' else ''
            for column, value in enumerate((index, label, status_text, detail)):
                item = self.flow_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.flow_table.setItem(row, column, item)
                if item.text() != str(value):
                    item.setText(str(value))
            # 状态着色：成功绿、失败红、执行中蓝、待执行灰（深色主题可读）
            status_item = self.flow_table.item(row, 2)
            if status_item is not None:
                # 深色主题下用中亮色阶，保证文字可读
                color = {
                    'success': '#97C459', 'failed': '#F09595',
                    'running': '#85B7EB', 'pending': '#B4B2A9',
                }.get(status)
                if color:
                    status_item.setForeground(QColor(color))

    def _on_row_stop(self):
        """Row button slot: read the task bound to the clicked button."""
        button = self.sender()
        if button is None:
            return
        self._stop_one(self._button_tasks.get(id(button)))

    def _stop_one(self, task):
        """Stop a single task, and always the executor's current task.

        A one-time task can be running *inside* another task (run_task_by_class
        calls task.run() in place without changing executor.current_task). In
        that case disabling the child alone is a no-op because framework
        check_enabled only reads current_task._enabled. Stopping the current
        task too guarantees the click actually takes effect.
        """
        if task is None:
            return
        try:
            task.disable()
        except Exception:
            pass
        try:
            if getattr(task, 'running', False):
                task.unpause()
        except Exception:
            pass
        # 仅当被点任务不是当前任务时才联动: 嵌套任务(run_task_by_class)的
        # _enabled 无人检查, 必须停当前任务才生效; 点当前任务自己已被 disable,
        # 点排队任务则不该误停正在跑的任务。
        current = getattr(self.executor, 'current_task', None) if self.executor else None
        if self.executor is not None and current is not None and task is not current:
            try:
                self.executor.stop_current_task()
            except Exception:
                pass
        # Defer refresh to the event loop: refreshing synchronously here would
        # destroy the very button whose click handler we are inside of.
        QTimer.singleShot(0, self._refresh)

    def _stop_group(self, kind):
        """Stop one group: 'trigger' or 'manual'."""
        if self.executor is None:
            return
        for task in self._visible_tasks():
            if self._is_trigger(task) != (kind == 'trigger'):
                continue
            try:
                task.disable()
            except Exception:
                pass
            try:
                if getattr(task, 'running', False):
                    task.unpause()
            except Exception:
                pass
        self._refresh()

    def _stop_all(self):
        if self.executor is None:
            return
        # Disable every task first: TaskExecutor skips disabled tasks in
        # next_task() and stops the current one on its next control check.
        for task in self._visible_tasks():
            try:
                task.disable()
            except Exception:
                pass
        try:
            self.executor.stop_current_task()
        except Exception:
            pass
        self._refresh()
