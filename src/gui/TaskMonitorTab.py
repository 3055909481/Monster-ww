"""Global monitor for manually triggered tasks.

Shows which tasks are currently running (there can be more than one on the
queue), their state/elapsed time/info, and one button to stop *all* manual
tasks. Registered as a custom tab so it works without touching the framework.
"""

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from Mon.core.icons import Icon
from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.widget.CustomTab import CustomTab


def _value_text(value):
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value)
    if isinstance(value, dict):
        return ', '.join(f'{key}={item}' for key, item in value.items())
    return '' if value is None else str(value)


class TaskMonitorTab(CustomTab):
    icon = Icon.APPLICATION

    def __init__(self):
        self._executor = None
        self.table = None
        self.stop_button = None
        super().__init__()
        self.setObjectName('TaskMonitorTab')
        self.tr_name = self.tr('任务监控')
        self.table = None
        self.stop_button = None
        self.empty_label = None
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

    def _build(self):
        body = QWidget(self.view)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.stop_button = Button(self.tr('停止所有手动任务'), icon='stop', variant='primary',
                                  parent=body)
        self.stop_button.clicked.connect(self._stop_all)
        header.addWidget(self.stop_button)
        header.addStretch(1)
        layout.addLayout(header)

        self.table = QTableWidget(body)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            self.tr('任务'), self.tr('状态'), self.tr('耗时'), self.tr('详情'),
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
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
        if self.table is None and value is not None:
            self._build()
        elif self.table is not None:
            self._refresh()

    def _manual_tasks(self):
        if self.executor is None:
            return []
        try:
            tasks = list(self.executor.get_all_tasks())
        except Exception:
            tasks = list(getattr(self.executor, 'onetime_tasks', []) or [])
        return [task for task in tasks
                if getattr(task, 'enabled', False) or getattr(task, 'running', False)]

    def _refresh(self):
        if self.table is None:
            return
        tasks = self._manual_tasks()
        current = getattr(self.executor, 'current_task', None) if self.executor else None
        self.stop_button.setEnabled(bool(tasks))
        self.table.setRowCount(len(tasks))
        now = time.time()
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
            values = [str(getattr(task, 'name', type(task).__name__)), state, elapsed, details]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def _stop_all(self):
        if self.executor is None:
            return
        # Disable every manual task first: TaskExecutor skips disabled tasks in
        # next_task() and stops the current one on its next control check.
        for task in self._manual_tasks():
            try:
                task.disable()
            except Exception:
                pass
        try:
            self.executor.stop_current_task()
        except Exception:
            pass
        self._refresh()
