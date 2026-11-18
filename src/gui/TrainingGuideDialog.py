"""Training-guide planner dialog.

Edit material-demand rows in a table, preview the computed run plan, and save
the result as a routine pipeline plan (so each multi-account entry can select
it like any other plan).

The heavy lifting lives in the pure ``TrainingGuide`` calculator; this file
only collects input and renders output.
"""

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.kit.dialogs import Dialog
from Mon.ui.qt.kit.feedback import show_toast
from Mon.ui.qt.kit.inputs import ComboBox

from src.task.routine import RoutineCatalog as catalog
from src.task.routine.RoutinePlans import (
    RoutinePlanStore, normalize_items,
)
from src.task.routine.TrainingGuide import (
    calculate, domain_meta, to_routine_items,
)

# table columns
COL_DOMAIN, COL_LOCATOR, COL_MATERIAL, COL_HAVE, COL_NEED, COL_PER_RUN, COL_DEL = range(7)
HEADERS = ['副本', '定位', '材料', '拥有', '需要', '每次产出', '']


def _locator_options(domain_id):
    """Return (keys, options-per-key) for a domain, from the live catalog."""
    if domain_id == 'simulation':
        return ['地区', '材料'], [catalog.simulation_regions(),
                                  catalog.simulation_options()]
    if domain_id == 'forgery':
        return ['地区', '武器类型'], [catalog.forgery_region_names(),
                                      catalog.forgery_weapon_types()]
    if domain_id == 'tacet':
        return ['无音区'], [catalog.tacet_zones()]
    if domain_id == 'boss_challenge':
        return ['BOSS'], [catalog.boss_challenges()]
    if domain_id == 'weekly_boss':
        return ['周本'], [catalog.weekly_display_names()]
    return [], []


class TrainingGuideDialog(Dialog):
    def __init__(self, parent, task):
        super().__init__(parent, task.tr('Training guide planner'),
                         ok_text=task.tr('Save plan'), cancel_text=task.tr('Cancel'), width=920)
        self.task = task
        self.store = RoutinePlanStore(task.executor.config_folder if task.executor else 'configs')
        self.meta = domain_meta()
        self._domain_ids = list(self.meta)
        self._domain_names = [self.meta[d]['name'] for d in self._domain_ids]
        self._loading = True

        root = QWidget(self.panel)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        hint = QLabel(task.tr(
            'Fill in each material shortage (need - have). Runs are computed as '
            'ceil(shortage / per-run). Rows targeting the same domain and target '
            'are merged. Set a stamina budget to cap the plan (0 = unlimited).'
        ), root)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        top = QHBoxLayout()
        top.addWidget(QLabel(task.tr('Plan name'), root))
        self.name_combo = ComboBox(list(self.store.load()), root)
        self.name_combo.setToolTip(task.tr('Existing plans can be overwritten'))
        top.addWidget(self.name_combo, 1)
        top.addWidget(QLabel(task.tr('Stamina budget'), root))
        self.budget = QSpinBox(root)
        self.budget.setRange(0, 100000)
        self.budget.setSingleStep(60)
        self.budget.setToolTip(task.tr('0 = unlimited'))
        self.budget.valueChanged.connect(self._on_changed)
        top.addWidget(self.budget)
        self.add_button = Button(task.tr('Add row'), parent=root)
        self.add_button.clicked.connect(self._add_row)
        top.addWidget(self.add_button)
        layout.addLayout(top)

        self.table = QTableWidget(root)
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_DEL, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        self.preview = QLabel('', root)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        self.body.addWidget(root)
        # start with one empty row
        self._add_row()
        self._loading = False
        self._on_changed()

    # -------------------------------------------------------------- rows
    def _add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)

        domain_combo = ComboBox(self._domain_names, self.table)
        domain_combo.currentIndexChanged.connect(self._on_domain_changed)
        self.table.setCellWidget(row, COL_DOMAIN, domain_combo)

        self.table.setItem(row, COL_LOCATOR, QTableWidgetItem(''))
        self.table.item(row, COL_LOCATOR).setFlags(Qt.ItemFlag.NoItemFlags)
        self.table.setItem(row, COL_MATERIAL, QTableWidgetItem(''))
        for col, default in ((COL_HAVE, 0), (COL_NEED, 0)):
            spin = QSpinBox(self.table)
            spin.setRange(0, 1000000)
            spin.valueChanged.connect(self._on_changed)
            self.table.setCellWidget(row, col, spin)
        per_run = QSpinBox(self.table)
        per_run.setRange(1, 100000)
        per_run.setValue(self.meta[self._domain_ids[0]]['default_yield'])
        per_run.valueChanged.connect(self._on_changed)
        self.table.setCellWidget(row, COL_PER_RUN, per_run)

        delete = Button(self.task.tr('Del'), parent=self.table)
        delete.clicked.connect(self._delete_row_by_sender)
        self.table.setCellWidget(row, COL_DEL, delete)

        self._apply_domain(row, self._domain_ids[0])
        if not self._loading:
            self._on_changed()

    def _delete_row_by_sender(self):
        sender = self.sender()
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, COL_DEL) is sender:
                self.table.removeRow(row)
                self._on_changed()
                return

    def _on_domain_changed(self):
        row = self._find_sender_row()
        if row is None:
            return
        domain_id = self._current_domain(row)
        self._apply_domain(row, domain_id)
        # refresh default per-run to the domain's default
        per_run = self.table.cellWidget(row, COL_PER_RUN)
        if per_run is not None:
            per_run.setValue(self.meta[domain_id]['default_yield'])
        self._on_changed()

    def _find_sender_row(self):
        sender = self.sender()
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, COL_DOMAIN) is sender:
                return row
        return None

    def _current_domain(self, row):
        combo = self.table.cellWidget(row, COL_DOMAIN)
        index = combo.currentIndex() if combo is not None else 0
        if 0 <= index < len(self._domain_ids):
            return self._domain_ids[index]
        return self._domain_ids[0]

    def _apply_domain(self, row, domain_id):
        """Replace the locator cell with dropdowns for this domain's keys."""
        keys, option_lists = _locator_options(domain_id)
        holder = QWidget(self.table)
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(2, 2, 2, 2)
        holder_layout.setSpacing(4)
        combos = []
        for key, options in zip(keys, option_lists):
            holder_layout.addWidget(QLabel(self.task.tr(key), holder))
            combo = ComboBox(options, holder)
            combo.currentIndexChanged.connect(self._on_changed)
            holder_layout.addWidget(combo, 1)
            combos.append((key, combo))
        holder._combos = combos
        self.table.setCellWidget(row, COL_LOCATOR, holder)

    def _locator_of(self, row):
        holder = self.table.cellWidget(row, COL_LOCATOR)
        if holder is None or not hasattr(holder, '_combos'):
            return {}
        return {key: combo.currentText() for key, combo in holder._combos}

    # -------------------------------------------------------------- logic
    def _demands(self):
        rows = []
        for row in range(self.table.rowCount()):
            per_run = self.table.cellWidget(row, COL_PER_RUN)
            have = self.table.cellWidget(row, COL_HAVE)
            need = self.table.cellWidget(row, COL_NEED)
            material_item = self.table.item(row, COL_MATERIAL)
            rows.append({
                'domain': self._current_domain(row),
                'locator': self._locator_of(row),
                'material': material_item.text().strip() if material_item else '',
                'have': have.value() if have else 0,
                'need': need.value() if need else 0,
                'per_run': per_run.value() if per_run else 1,
            })
        return rows

    def _on_changed(self):
        if self._loading:
            return
        try:
            steps, total, skipped = calculate(self._demands(), self.budget.value(), self.meta)
        except ValueError as exc:
            self.preview.setText(self.task.tr('Input error: {error}').format(error=exc))
            return
        if not steps:
            self.preview.setText(self.task.tr('No shortage detected. Nothing to plan.'))
            return
        lines = [self.task.tr('Planned runs:')]
        for step in steps:
            name = self.meta[step['domain']]['name']
            target = ', '.join(f'{key}={value}' for key, value in step['locator'].items())
            lines.append(f"  {name} [{target}] × {step['runs']} ({step['stamina'] * step['runs']} stamina)")
        lines.append(self.task.tr('Total stamina: {total}').format(total=total))
        if skipped:
            lines.append(self.task.tr('Over budget, skipped: {count} row(s)').format(count=len(skipped)))
        self.preview.setText('\n'.join(lines))

    # -------------------------------------------------------------- dialog
    def validate(self):
        try:
            steps, total, skipped = calculate(self._demands(), self.budget.value(), self.meta)
        except ValueError as exc:
            show_toast(self.parentWidget(), self.task.tr('Input error: {error}').format(error=exc),
                       tone='warning')
            return False
        if not steps:
            show_toast(self.parentWidget(), self.task.tr('No runs to save'), tone='warning')
            return False

        name = self.name_combo.currentText().strip() or self.task.tr('Training plan')
        plans = self.store.load()
        items = normalize_items(to_routine_items(steps))
        # append to the chosen plan so reward/other steps are preserved
        existing = plans.get(name, [])
        combined = normalize_items(list(existing) + items)
        plans[name] = combined
        saved = self.store.save(plans)

        self.task.config['Routine Plan'] = name
        config_type = self.task.config_type.get('Routine Plan')
        if isinstance(config_type, dict) and 'options' in config_type:
            config_type['options'] = list(saved)
        show_toast(self.parentWidget(),
                   self.task.tr('Training plan saved as "{name}"').format(name=name), tone='success')
        return True
