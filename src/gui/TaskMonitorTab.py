"""Global monitor for running tasks.

Two separate groups are shown: trigger-style tasks (auto combat, auto pick,
auto login, ... which stay resident) and manually queued one-time tasks.
Every row has its own stop button, and names are shown translated (Chinese).
Registered as a custom tab so the framework itself is untouched.
"""

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from Mon import og, TriggerTask
from Mon.core.icons import Icon
from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.widget.CustomTab import CustomTab


def _value_text(value):
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value)
    if isinstance(value, dict):
        return ', '.join(f'{key}={item}' for key, item in value.items())
    return '' if value is None else str(value)


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
    icon = Icon.APPLICATION

    def __init__(self):
        self._executor = None
        self.manual_table = None
        self.trigger_table = None
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
        table.horizontalHeader().setStretchLastSection(True)
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
        self.trigger_stop_all = QPushButton(self.tr('停止全部'), body)
        self.trigger_stop_all.clicked.connect(lambda: self._stop_group('trigger'))
        trigger_header.addWidget(self.trigger_stop_all)
        layout.addLayout(trigger_header)
        self.trigger_table = self._make_table(body)
        layout.addWidget(self.trigger_table, 1)

        # Manually queued one-time tasks.
        manual_header = QHBoxLayout()
        manual_header.addWidget(QLabel(self.tr('手动任务'), body))
        manual_header.addStretch(1)
        self.manual_stop_all = QPushButton(self.tr('停止全部'), body)
        self.manual_stop_all.clicked.connect(lambda: self._stop_group('manual'))
        manual_header.addWidget(self.manual_stop_all)
        layout.addLayout(manual_header)
        self.manual_table = self._make_table(body)
        layout.addWidget(self.manual_table, 1)

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
        # Keep enabled or running tasks; disabled/stopped ones drop off the view
        # (same behaviour as before this refactor).
        return [task for task in tasks
                if getattr(task, 'enabled', False) or getattr(task, 'running', False)]

    @staticmethod
    def _is_trigger(task):
        return isinstance(task, TriggerTask)

    def _fill(self, table, tasks, current, now):
        # Reset to zero first so Qt disposes the previous row cell-widgets
        # (stop buttons); otherwise widgets pile up on every 1s refresh.
        table.setRowCount(0)
        table.setRowCount(len(tasks))
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
                table.setItem(row, column, QTableWidgetItem(value))
            button = QPushButton(self.tr('停止'), table)
            # Default argument binds the task so the lambda is not late-binding.
            button.clicked.connect(lambda _checked=False, t=task: self._stop_one(t))
            table.setCellWidget(row, 4, button)

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
        has_tasks = bool(tasks)
        self.stop_button.setEnabled(has_tasks)
        self.trigger_stop_all.setEnabled(bool(trigger))
        self.manual_stop_all.setEnabled(bool(manual))

    def _stop_one(self, task):
        """Stop a single task without touching the others."""
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
        self._refresh()

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
