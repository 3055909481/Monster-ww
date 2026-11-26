"""Custom tab that exposes the user-defined task flow.

Shows the current flow and a read-only list of its steps, with buttons to edit
the flow and run it. Editing happens in :class:`CustomFlowDialog`.
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

from src.task.CustomFlowTask import CustomFlowTask
from src.task.routine.FlowSteps import (
    DEFAULT_FLOW_NAME, ENTRIES_BY_ID, FLOW_SELECTOR, FlowStepStore,
    describe_params,
)


class _ElidedLabel(Label):
    """A label that shortens its text instead of widening its row."""

    def __init__(self, text='', role='secondary', parent=None):
        super().__init__(text, role, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text):
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.color('separator'))


class _StepRow(QWidget):
    """One read-only flow row: order, icon, name and settings summary."""

    def __init__(self, order, entry, params, enabled, parent=None):
        super().__init__(parent)
        self.setObjectName('customFlowStepRow')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 7, 6, 7)
        layout.setSpacing(10)

        index = Label(str(order), 'caption', self)
        index.setFixedWidth(18)
        index.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(index)

        glyph = QLabel(self)
        glyph.setFixedSize(18, 18)
        glyph.setPixmap(kit_icon(entry.icon, 'text_secondary').pixmap(
            QSize(16, 16), self.devicePixelRatioF()))
        layout.addWidget(glyph)

        name = Label(entry.name, 'strong' if enabled else 'secondary', self)
        layout.addWidget(name, 0)

        summary = _ElidedLabel(' · '.join(describe_params(entry, params)), 'secondary', self)
        summary.setToolTip(summary._full_text)
        layout.addWidget(summary, 1)

        if not enabled:
            chip = Label(self.tr('未启用'), 'caption', self)
            layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
            for widget in (index, glyph, name, summary, chip):
                widget.setEnabled(False)


class CustomFlowTab(CustomTab):
    def __init__(self):
        self._executor = None
        self.task = None
        self.rows_host = None
        self.run_button = None
        self.edit_button = None
        self.flow_combo = None
        super().__init__()
        self.setObjectName('CustomFlowTab')
        self.tr_name = self.tr('自定义任务流')
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
        self.task = self.get_task(CustomFlowTask) if value is not None else None
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
                widget.setParent(None)
                widget.deleteLater()

    def _render(self):
        self._clear()
        self.run_button = None
        self.flow_combo = None
        if self.task is None:
            self.vBoxLayout.addWidget(Label(self.tr('自定义任务流任务不可用。'), 'secondary', self.view))
            self.vBoxLayout.addStretch(1)
            return

        store = FlowStepStore(self.executor.config_folder)
        flows = store.load()
        flow_name = self.task.config.get(FLOW_SELECTOR, DEFAULT_FLOW_NAME)
        if flow_name not in flows:
            flow_name = DEFAULT_FLOW_NAME
            self.task.config[FLOW_SELECTOR] = flow_name
        items = flows.get(flow_name, [])
        enabled_count = sum(1 for item in items if item.get('enabled'))

        card = Card(look='inset', parent=self.view)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(4)
        title = Label(self.tr('自定义任务流'), 'title', card)
        subtitle = Label(
            self.tr('按自定义顺序执行：启动国服/国际服、登录账号、执行任务、切换账号、退出客户端。'),
            'secondary', card, word_wrap=True)
        text.addWidget(title)
        text.addWidget(subtitle)
        header.addLayout(text, 1)
        self.edit_button = Button(self.tr('编辑任务流'), icon='pen', parent=card)
        self.edit_button.clicked.connect(self._edit_steps)
        self.run_button = Button(self.tr('运行任务'), icon='play', variant='primary', parent=card)
        self.run_button.clicked.connect(self._start_task)
        for button in (self.edit_button, self.run_button):
            header.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        flow_row = QHBoxLayout()
        flow_row.setSpacing(8)
        flow_row.addWidget(Label(self.tr('当前任务流'), 'secondary'), 0)
        self.flow_combo = ComboBox(list(flows), card)
        self.flow_combo.setFixedWidth(200)
        self.flow_combo.setCurrentText(flow_name)
        self.flow_combo.currentTextChanged.connect(self._flow_changed)
        flow_row.addWidget(self.flow_combo, 0)
        summary = Label(f'{len(items)} 个步骤 · 启用 {enabled_count}', 'caption', card)
        flow_row.addWidget(summary, 0)
        flow_row.addStretch(1)
        layout.addLayout(flow_row)
        layout.addWidget(_Hairline(card))

        body = QWidget(card)
        self.rows_host = QVBoxLayout(body)
        self.rows_host.setContentsMargins(0, 0, 0, 0)
        self.rows_host.setSpacing(2)
        self._fill_rows(items)
        layout.addWidget(body, 1)

        if not enabled_count:
            hint = Label(self.tr('当前任务流没有启用的步骤，点「编辑任务流」勾选要执行的步骤。'),
                         'caption', card, word_wrap=True)
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
            self.rows_host.addWidget(Label(self.tr('任务流为空，点「编辑任务流」添加步骤。'),
                                           'secondary', self.view, word_wrap=True))

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
        show_toast(self, self.tr('已启动自定义任务流'), tone='success')
        self._sync_run_button()

    def _edit_steps(self):
        self.task.configure_flows(on_saved=self._render)

    def _flow_changed(self, name):
        if self.task is None or not name:
            return
        self.task.config[FLOW_SELECTOR] = name
        QTimer.singleShot(0, self._render)
        show_toast(self, self.tr('已切换任务流'), tone='success')
