"""Editor dialog for daily routine plans.

Left: checkable, drag-reorderable step list. Right: config rows for the
selected step, rendered with the framework's own config editors.
"""

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.kit.dialogs import Dialog
from Mon.ui.qt.kit.feedback import show_toast
from Mon.ui.qt.kit.inputs import ComboBox
from Mon.ui.qt.tasks.config_form import ConfigForm

from src.task.routine.RoutinePlans import (
    DEFAULT_PLAN_NAME, ENTRIES, ENTRIES_BY_ID, PLAN_SELECTOR, PlanItemConfig,
    RoutinePlanStore, default_items, new_item, normalize_items,
)

ID_ROLE = Qt.UserRole
PARAMS_ROLE = Qt.UserRole + 1
UID_ROLE = Qt.UserRole + 2


class _PlanItemStub:
    """Minimal task-like object required by ConfigForm."""

    def __init__(self, entry):
        self.default_config = entry.default_config
        self.config_description = entry.config_description
        self.config_type = entry.config_type

    def get_global_config_desc(self, key):
        return None

    def get_global_config(self, key):
        return None


class RoutinePlanDialog(Dialog):
    def __init__(self, parent, task):
        super().__init__(parent, task.tr('Daily Routine Pipeline'),
                         ok_text=task.tr('Save and Close'), cancel_text=task.tr('Cancel'), width=1000)
        self.task = task
        self.store = RoutinePlanStore(task.executor.config_folder if task.executor else 'configs')
        self.plans = self.store.load()
        self.active_name = self.task.config.get(PLAN_SELECTOR, DEFAULT_PLAN_NAME)
        if self.active_name not in self.plans:
            self.active_name = DEFAULT_PLAN_NAME
        self._loading = True

        root = QWidget(self.panel)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        left = QWidget(root)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        plan_row = QHBoxLayout()
        self.plan_combo = ComboBox(list(self.plans))
        self.plan_combo.setCurrentText(self.active_name)
        self.plan_combo.currentTextChanged.connect(self._plan_changed)
        plan_row.addWidget(QLabel(task.tr('Plan')), 0)
        plan_row.addWidget(self.plan_combo, 1)
        self.new_button = Button(task.tr('New'), parent=left)
        self.new_button.clicked.connect(self._new_plan)
        self.delete_button = Button(task.tr('Delete'), variant='subtle', parent=left)
        self.delete_button.clicked.connect(self._delete_plan)
        plan_row.addWidget(self.new_button)
        plan_row.addWidget(self.delete_button)
        left_layout.addLayout(plan_row)

        self.step_list = QListWidget(left)
        self.step_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.step_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.step_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_list.currentItemChanged.connect(self._item_changed)
        self.step_list.itemChanged.connect(self._item_checked)
        left_layout.addWidget(self.step_list, 1)

        order_row = QHBoxLayout()
        self.add_combo = ComboBox([entry.name for entry in ENTRIES])
        self.add_combo.setToolTip(task.tr('Choose a step to add'))
        order_row.addWidget(self.add_combo, 1)
        self.add_button = QPushButton(task.tr('Add Step'), left)
        self.add_button.clicked.connect(self._add_step)
        order_row.addWidget(self.add_button)
        left_layout.addLayout(order_row)

        order_row2 = QHBoxLayout()
        self.up_button = QPushButton(task.tr('Move Up'), left)
        self.down_button = QPushButton(task.tr('Move Down'), left)
        self.delete_step_button = QPushButton(task.tr('Delete Step'), left)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.delete_step_button.clicked.connect(self._delete_step)
        order_row2.addWidget(self.up_button)
        order_row2.addWidget(self.down_button)
        order_row2.addWidget(self.delete_step_button)
        left_layout.addLayout(order_row2)

        right = QWidget(root)
        self.right_layout = QVBoxLayout(right)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.config_host = QWidget(right)
        self.config_layout = QVBoxLayout(self.config_host)
        self.config_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.addWidget(self.config_host, 1)
        self.hint = QLabel(task.tr('Check a step to include it. Drag to reorder. Runs top to bottom.'), right)
        self.hint.setWordWrap(True)
        self.right_layout.addWidget(self.hint)

        layout.addWidget(left, 1)
        layout.addWidget(right, 1)
        self.body.addWidget(root)
        self._load_steps()
        self._loading = False

    # -------------------------------------------------------------- helpers
    def _current_items(self):
        return self.plans.get(self.active_name, default_items())

    def _load_steps(self):
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for item in self._current_items():
            entry = ENTRIES_BY_ID[item['id']]
            widget_item = QListWidgetItem(entry.name)
            widget_item.setFlags(widget_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            widget_item.setCheckState(Qt.CheckState.Checked if item['enabled'] else Qt.CheckState.Unchecked)
            widget_item.setData(ID_ROLE, item['id'])
            widget_item.setData(UID_ROLE, item.get('uid'))
            widget_item.setData(PARAMS_ROLE, deepcopy(item['params']))
            widget_item.setToolTip(entry.name)
            self.step_list.addItem(widget_item)
        self.step_list.blockSignals(False)
        self._render_config(self.step_list.item(0) if self.step_list.count() else None)

    def _collect_steps(self):
        items = []
        for index in range(self.step_list.count()):
            widget_item = self.step_list.item(index)
            task_id = widget_item.data(ID_ROLE)
            entry = ENTRIES_BY_ID[task_id]
            params = deepcopy(entry.default_config)
            params.update(widget_item.data(PARAMS_ROLE) or {})
            items.append({'id': task_id,
                          'uid': widget_item.data(UID_ROLE),
                          'enabled': widget_item.checkState() == Qt.CheckState.Checked,
                          'params': params})
        return normalize_items(items)

    def _render_config(self, widget_item):
        while self.config_layout.count():
            old = self.config_layout.takeAt(0)
            if old.widget() is not None:
                old.widget().deleteLater()
        if widget_item is None:
            return
        entry = ENTRIES_BY_ID[widget_item.data(ID_ROLE)]
        if not entry.default_config:
            label = QLabel(self.task.tr('This step has no settings.'), self.config_host)
            label.setWordWrap(True)
            self.config_layout.addWidget(label)
            return
        params = deepcopy(widget_item.data(PARAMS_ROLE) or entry.default_config)

        def on_change(new_values):
            widget_item.setData(PARAMS_ROLE, deepcopy(new_values))

        config = PlanItemConfig(params, entry.default_config, on_change)
        form = ConfigForm(_PlanItemStub(entry), config, entry.default_config,
                          entry.config_description, entry.config_type, self.config_host)
        self.config_layout.addWidget(form)
        self.config_layout.addStretch(1)

    # -------------------------------------------------------------- slots
    def _item_changed(self, current, previous):
        if self._loading:
            return
        if previous is not None:
            previous.setData(PARAMS_ROLE, previous.data(PARAMS_ROLE))
        self.plans[self.active_name] = self._collect_steps()
        self._render_config(current)

    def _item_checked(self, widget_item):
        if not self._loading:
            self.plans[self.active_name] = self._collect_steps()

    def _move(self, delta):
        row = self.step_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.step_list.count():
            return
        self._loading = True
        item = self.step_list.takeItem(row)
        self.step_list.insertItem(target, item)
        self.step_list.setCurrentRow(target)
        self._loading = False
        self.plans[self.active_name] = self._collect_steps()

    def _add_step(self):
        name = self.add_combo.currentText()
        task_id = next((entry.task_id for entry in ENTRIES if entry.name == name), None)
        if task_id is None:
            return
        item = new_item(task_id)
        widget_item = QListWidgetItem(ENTRIES_BY_ID[task_id].name)
        widget_item.setFlags(widget_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        widget_item.setCheckState(Qt.CheckState.Checked)
        widget_item.setData(ID_ROLE, task_id)
        widget_item.setData(UID_ROLE, item['uid'])
        widget_item.setData(PARAMS_ROLE, deepcopy(item['params']))
        widget_item.setToolTip(ENTRIES_BY_ID[task_id].name)
        self.step_list.addItem(widget_item)
        self.step_list.setCurrentItem(widget_item)
        self.plans[self.active_name] = self._collect_steps()

    def _delete_step(self):
        row = self.step_list.currentRow()
        if row < 0:
            return
        self._loading = True
        self.step_list.takeItem(row)
        self._loading = False
        self.plans[self.active_name] = self._collect_steps()
        if self.step_list.count():
            self.step_list.setCurrentRow(min(row, self.step_list.count() - 1))
        else:
            self._render_config(None)

    def _plan_changed(self, name):
        if self._loading or not name:
            return
        self._loading = True
        self.plans[self.active_name] = self._collect_steps()
        self.active_name = name
        self._load_steps()
        self._loading = False

    def _new_plan(self):
        name, ok = QInputDialog.getText(self, self.task.tr('New plan'), self.task.tr('Plan name'))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.plans:
            show_toast(self, self.task.tr('Plan already exists'), tone='warning')
            return
        self.plans[self.active_name] = self._collect_steps()
        self.plans[name] = default_items()
        self.plan_combo.blockSignals(True)
        self.plan_combo.addItem(name)
        self.plan_combo.setCurrentText(name)
        self.plan_combo.blockSignals(False)
        self._loading = True
        self.active_name = name
        self._load_steps()
        self._loading = False

    def _delete_plan(self):
        if len(self.plans) <= 1:
            show_toast(self, self.task.tr('Keep at least one plan'), tone='warning')
            return
        name = self.active_name
        self.plans.pop(name, None)
        self.plan_combo.blockSignals(True)
        self.plan_combo.removeItem(self.plan_combo.findText(name))
        self.plan_combo.blockSignals(False)
        self._loading = True
        self.active_name = self.plan_combo.currentText()
        self._load_steps()
        self._loading = False

    # -------------------------------------------------------------- dialog
    def _fit(self):
        window = self.parentWidget()
        if window is not None:
            self.panel.setFixedWidth(min(1040, max(560, window.width() - 48)))
        self.step_list.setMinimumHeight(280)
        super()._fit()

    def validate(self):
        self.plans[self.active_name] = self._collect_steps()
        self.store.save(self.plans)
        self.task.config[PLAN_SELECTOR] = self.active_name
        config_type = self.task.config_type.get(PLAN_SELECTOR)
        if isinstance(config_type, dict) and 'options' in config_type:
            config_type['options'] = list(self.plans)
        show_toast(self.parentWidget(), self.task.tr('Routine plan saved'), tone='success')
        return True
