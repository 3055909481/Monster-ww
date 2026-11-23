"""Custom tab that exposes the daily routine pipeline.

Follows the app's task page: a header with the current plan and a primary run
button, then the step order as read-only rows that summarise each step's
settings at a glance. Editing happens in :class:`RoutinePlanDialog`.
"""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.kit.cards import Card
from Mon.ui.qt.kit.feedback import show_toast
from Mon.ui.qt.kit.icons import icon as kit_icon
from Mon.ui.qt.kit.inputs import ComboBox
from Mon.ui.qt.kit.labels import Label
from Mon.ui.qt.kit.theme import theme
from Mon.ui.qt.widget.CustomTab import CustomTab

from src.task.RoutineTask import RoutineTask
from src.task.routine.RoutinePlans import (
    DEFAULT_PLAN_NAME, ENTRIES_BY_ID, PLAN_SELECTOR, RoutinePlanStore,
    describe_params, stamina_text,
)


class _ElidedLabel(Label):
    """A label that shortens its text instead of widening its row."""

    def __init__(self, text: str = '', role: str = 'secondary', parent=None):
        super().__init__(text, role, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:
        self._full_text = text
        super().setText(text)
        self._apply_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight,
                                           max(0, self.width())))


class _Hairline(QWidget):
    """1px divider painted from the theme's separator token.

    Painting at paint time keeps the line correct after a light/dark switch,
    which a captured stylesheet colour would not.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.color('separator'))


class _StepRow(QWidget):
    """One read-only pipeline row: order, icon, name, settings summary, cost."""

    def __init__(self, order, entry, params, enabled, parent=None):
        super().__init__(parent)
        self.setObjectName('routineStepRow')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 7, 6, 7)
        layout.setSpacing(10)

        index = Label(str(order), 'caption', self)
        index.setFixedWidth(18)
        index.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(index)

        glyph = QLabel(self)
        glyph.setFixedSize(18, 18)
        icon = kit_icon(entry.icon, 'text_secondary')
        glyph.setPixmap(icon.pixmap(QSize(16, 16), self.devicePixelRatioF()))
        layout.addWidget(glyph)

        name = Label(entry.name, 'strong' if enabled else 'secondary', self)
        layout.addWidget(name, 0)

        summary = _ElidedLabel(' · '.join(describe_params(entry, params)), 'secondary', self)
        summary.setToolTip(summary._full_text)
        layout.addWidget(summary, 1)

        caption = stamina_text(entry) if enabled else ''
        chip_text = caption or ('未启用' if not enabled else '')
        chip = Label(chip_text, 'caption', self)
        chip.setVisible(bool(chip_text))
        layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setEnabled(True)
        if not enabled:
            for widget in (index, glyph, name, summary, chip):
                widget.setEnabled(False)


class RoutineTab(CustomTab):
    def __init__(self):
        self._executor = None
        self.task = None
        self.rows_host = None
        self.run_button = None
        self.edit_button = None
        self.guide_button = None
        self.plan_combo = None
        super().__init__()
        self.setObjectName('RoutineTab')
        self.tr_name = self.tr('日常一条龙')
        self.run_timer = QTimer(self)
        self.run_timer.setInterval(1000)
        self.run_timer.timeout.connect(self._sync_run_button)

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

    def showEvent(self, event):
        super().showEvent(event)
        self.run_timer.start()

    def hideEvent(self, event):
        self.run_timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------ view
    def _clear(self):
        while self.vBoxLayout.count():
            old = self.vBoxLayout.takeAt(0)
            widget = old.widget()
            if widget is not None:
                # Detach first: deleteLater only runs on the next event loop
                # turn, and until then the widget would still paint at its old
                # geometry over the rebuilt card.
                widget.setParent(None)
                widget.deleteLater()

    def _render(self):
        self._clear()
        # The widgets from the previous render are gone; drop the references so
        # helpers such as _sync_run_button cannot touch a deleted C++ object.
        self.run_button = None
        self.plan_combo = None
        if self.task is None:
            self.vBoxLayout.addWidget(Label(self.tr('流水线任务不可用。'), 'secondary', self.view))
            self.vBoxLayout.addStretch(1)
            return

        store = RoutinePlanStore(self.executor.config_folder)
        plans = store.load()
        plan_name = self.task.config.get(PLAN_SELECTOR, DEFAULT_PLAN_NAME)
        if plan_name not in plans:
            plan_name = DEFAULT_PLAN_NAME
            self.task.config[PLAN_SELECTOR] = plan_name
        items = plans.get(plan_name, [])
        enabled_count = sum(1 for item in items if item.get('enabled'))

        card = Card(look='inset', parent=self.view)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(4)
        title = Label(self.tr('日常一条龙'), 'title', card)
        subtitle = Label(self.tr('按顺序执行领奖与刷体力步骤；计划保存在本地，多账号可各自选用不同计划。'),
                         'secondary', card, word_wrap=True)
        text.addWidget(title)
        text.addWidget(subtitle)
        header.addLayout(text, 1)
        self.guide_button = Button(self.tr('培养目标'), icon='tasks', variant='subtle', parent=card)
        self.guide_button.setToolTip(self.tr('根据材料缺口规划体力副本'))
        self.guide_button.clicked.connect(self._open_training_guide)
        self.edit_button = Button(self.tr('编辑计划'), icon='pen', parent=card)
        self.edit_button.clicked.connect(self._edit_steps)
        self.run_button = Button(self.tr('运行任务'), icon='play', variant='primary', parent=card)
        self.run_button.clicked.connect(self._start_task)
        for button in (self.guide_button, self.edit_button, self.run_button):
            header.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        plan_row = QHBoxLayout()
        plan_row.setSpacing(8)
        plan_row.addWidget(Label(self.tr('当前计划'), 'secondary'), 0)
        self.plan_combo = ComboBox(list(plans), card)
        self.plan_combo.setFixedWidth(200)
        self.plan_combo.setCurrentText(plan_name)
        self.plan_combo.currentTextChanged.connect(self._plan_changed)
        plan_row.addWidget(self.plan_combo, 0)
        summary = Label(f'{len(items)} 个步骤 · 启用 {enabled_count}', 'caption', card)
        plan_row.addWidget(summary, 0)
        plan_row.addStretch(1)
        layout.addLayout(plan_row)
        layout.addWidget(_Hairline(card))

        body = QWidget(card)
        self.rows_host = QVBoxLayout(body)
        self.rows_host.setContentsMargins(0, 0, 0, 0)
        self.rows_host.setSpacing(2)
        self._fill_rows(items)
        layout.addWidget(body, 1)

        if not enabled_count:
            hint = Label(self.tr('当前计划没有启用的步骤，点「编辑计划」勾选要跑的步骤。'), 'caption', card,
                         word_wrap=True)
            layout.addWidget(hint)

        self.vBoxLayout.addWidget(card)
        self.vBoxLayout.addStretch(1)
        self._sync_run_button()

    def _fill_rows(self, items):
        while self.rows_host.count():
            old = self.rows_host.takeAt(0)
            widget = old.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        order = 0
        for item in items:
            entry = ENTRIES_BY_ID.get(item.get('id'))
            if entry is None:
                continue
            order += 1
            row = _StepRow(order, entry, item.get('params') or {}, bool(item.get('enabled')), self.view)
            self.rows_host.addWidget(row)
        if not order:
            self.rows_host.addWidget(Label(self.tr('计划为空，点「编辑计划」添加步骤。'), 'secondary',
                                         self.view, word_wrap=True))

    # --------------------------------------------------------------- actions
    def _sync_run_button(self):
        button = self.run_button
        if button is None:
            return
        running = bool(self.task is not None and self.task.enabled)
        try:
            button.setEnabled(self.task is not None and not running)
            button.setText(self.tr('运行中…') if running else self.tr('运行任务'))
        except RuntimeError:
            # Page was rebuilt while the timer was running.
            self.run_button = None

    def _start_task(self):
        if self.task is None:
            return
        if self.task.enabled:
            show_toast(self, self.tr('任务已在运行'), tone='warning')
            return
        try:
            from Mon import og
            og.app.start_controller.start(self.task)
        except Exception as error:
            show_toast(self, self.tr('无法启动任务：') + str(error), tone='danger', duration=5000)
            return
        show_toast(self, self.tr('已启动日常一条龙'), tone='success')
        self._sync_run_button()

    def _edit_steps(self):
        # Re-render after save so both the rows and the plan dropdown reflect
        # plans/steps added or renamed in the editor.
        self.task.configure_plans(on_saved=self._render)

    def _open_training_guide(self):
        self.task.configure_training_guide(on_saved=self._render)

    def _plan_changed(self, name):
        if self.task is None or not name:
            return
        self.task.config[PLAN_SELECTOR] = name
        # Rebuilding the page destroys this combo box, so leave the signal
        # handler before touching the widget tree.
        QTimer.singleShot(0, self._render)
        show_toast(self, self.tr('已切换计划'), tone='success')
