"""Custom tab that exposes the daily routine pipeline editor."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.kit.feedback import show_toast
from Mon.ui.qt.kit.inputs import ComboBox
from Mon.ui.qt.widget.Card import Card
from Mon.ui.qt.widget.CustomTab import CustomTab

from src.task.RoutineTask import RoutineTask
from src.task.routine.RoutinePlans import (
    DEFAULT_PLAN_NAME, ENTRIES_BY_ID, PLAN_SELECTOR, RoutinePlanStore,
)


class RoutineTab(CustomTab):
    def __init__(self):
        super().__init__()
        self.setObjectName('RoutineTab')
        self.tr_name = self.tr('Daily Routine')
        self.task = None

    @property
    def name(self):
        return self.tr_name

    @property
    def executor(self):
        return self._executor

    @executor.setter
    def executor(self, value):
        self._executor = value
        self.task = self.get_task(RoutineTask) if value is not None else None
        self._render()

    def _render(self):
        while self.vBoxLayout.count():
            old = self.vBoxLayout.takeAt(0)
            if old.widget() is not None:
                old.widget().deleteLater()
        if self.task is None:
            self.vBoxLayout.addWidget(QLabel(self.tr('Routine task is unavailable.')))
            return

        card = Card(self.tr('Daily Routine Pipeline'), QWidget(), parent=self.view)
        layout = QVBoxLayout(card.widget)

        intro = QLabel(self.tr(
            'Build an ordered pipeline of reward claims and stamina runs. '
            'The plan is saved locally; multi-account users can pick a different plan for each account.'
        ))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        store = RoutinePlanStore(self.executor.config_folder)
        plans = store.load()
        plan_row = QWidget(card)
        plan_layout = QHBoxLayout(plan_row)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        self.plan_combo = ComboBox(list(plans))
        self.plan_combo.setCurrentText(self.task.config.get(PLAN_SELECTOR, DEFAULT_PLAN_NAME))
        self.plan_combo.currentTextChanged.connect(self._plan_changed)
        plan_layout.addWidget(QLabel(self.tr('Active plan')), 0)
        plan_layout.addWidget(self.plan_combo, 1)
        edit_button = Button(self.tr('Edit steps'), icon='pen', parent=plan_row)
        edit_button.clicked.connect(self.task.configure_plans)
        plan_layout.addWidget(edit_button)
        layout.addWidget(plan_row)

        self.step_list = QListWidget(card)
        self.step_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self.step_list, 1)
        self._fill_steps(plans.get(self.plan_combo.currentText(), []))
        self.vBoxLayout.addWidget(card)

    def _fill_steps(self, items):
        self.step_list.clear()
        for item in items:
            if not item.get('enabled'):
                continue
            entry = ENTRIES_BY_ID.get(item['id'])
            if entry is None:
                continue
            label = f"• {entry.name}"
            params = item.get('params') or {}
            if params:
                label += '  —  ' + ', '.join(f'{key}: {value}' for key, value in params.items())
            list_item = QListWidgetItem(label)
            list_item.setToolTip(entry.name)
            self.step_list.addItem(list_item)

    def _plan_changed(self, name):
        self.task.config[PLAN_SELECTOR] = name
        plans = RoutinePlanStore(self.executor.config_folder).load()
        self._fill_steps(plans.get(name, []))
        show_toast(self, self.tr('Active plan changed'), tone='success')
