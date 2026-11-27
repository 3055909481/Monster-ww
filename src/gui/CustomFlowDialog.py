"""Editor dialog for custom task flows.

Mirrors RoutinePlanDialog: a checkable, drag-reorderable step list on the left
and a ConfigForm detail panel on the right. Steps drive the full client
lifecycle (start/log-in/run/exit) instead of only a daily routine.
"""

from copy import deepcopy

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QInputDialog, QListWidget,
    QListWidgetItem, QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from Mon.ui.qt.kit.buttons import Button
from Mon.ui.qt.kit.cards import Card, SettingRow
from Mon.ui.qt.kit.dialogs import Dialog
from Mon.ui.qt.kit.feedback import show_toast
from Mon.ui.qt.kit.icons import icon as kit_icon
from Mon.ui.qt.kit.inputs import ComboBox
from Mon.ui.qt.kit.labels import Label
from Mon.ui.qt.kit.tabs import TabStrip
from Mon.ui.qt.kit.theme import theme
from Mon.ui.qt.tasks.config_form import ConfigForm

from src.task.routine.FlowSteps import (
    DEFAULT_FLOW_NAME, ENTRIES, ENTRIES_BY_ID, FLOW_SELECTOR, FlowItemConfig,
    FlowStepStore, default_items, describe_params, new_item, normalize_items,
)

ID_ROLE = Qt.UserRole
PARAMS_ROLE = Qt.UserRole + 1
UID_ROLE = Qt.UserRole + 2

ROW_HEIGHT = 40
CHECK_SIZE = 16
GRIP_LEFT = 12
CHECK_LEFT = 30
ICON_LEFT = 56
TEXT_LEFT = 78


def _accent_fill(alpha):
    color = theme.color('accent')
    color.setAlphaF(alpha)
    return color


class _FlowItemStub:
    """Minimal task-like object required by ConfigForm with dynamic options."""

    def __init__(self, entry, config_type):
        self.default_config = entry.default_config
        self.config_description = entry.config_description
        self.config_type = config_type

    def get_global_config_desc(self, key):
        return None

    def get_global_config(self, key):
        return None


class _StepDelegate(QStyledItemDelegate):
    """Paints drag grip, check box, step icon, name and settings summary."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view

    def _row_rect(self, rect):
        return QRectF(rect).adjusted(3, 1, -3, -1)

    def _checkbox_rect(self, rect):
        inner = self._row_rect(rect)
        return QRectF(inner.left() + CHECK_LEFT,
                      inner.center().y() - CHECK_SIZE / 2, CHECK_SIZE, CHECK_SIZE)

    def sizeHint(self, option, index):
        return QSize(0, ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inner = self._row_rect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected or hovered:
            fill = _accent_fill(0.18) if selected else theme.color('control_hover')
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(inner, 8, 8)

        entry = ENTRIES_BY_ID.get(index.data(ID_ROLE))
        checked = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole)) == Qt.CheckState.Checked

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color('text_tertiary'))
        for row in range(3):
            for column in range(2):
                painter.drawEllipse(QRectF(inner.left() + GRIP_LEFT + column * 5,
                                           inner.center().y() - 6 + row * 6, 2.4, 2.4))

        box = self._checkbox_rect(option.rect)
        if checked:
            painter.setBrush(theme.color('accent'))
            painter.setPen(theme.color('accent'))
            painter.drawRoundedRect(box, 5, 5)
            kit_icon('check', 'on_accent').paint(painter, box.toRect(), Qt.AlignmentFlag.AlignCenter)
        else:
            painter.setBrush(theme.color('control'))
            painter.setPen(theme.color('switch_off'))
            painter.drawRoundedRect(box, 5, 5)

        if entry is not None:
            icon_rect = QRectF(inner.left() + ICON_LEFT, inner.center().y() - 8, 16, 16)
            try:
                kit_icon(entry.icon, 'text_secondary').paint(painter, icon_rect.toRect(),
                                                           Qt.AlignmentFlag.AlignCenter)
            except Exception:
                # Never let a bad icon erase the step label in the paint event.
                pass
            name_left = inner.left() + TEXT_LEFT
            name_width = inner.right() - 14 - name_left
            painter.setPen(theme.color('text') if checked else theme.color('text_secondary'))
            metrics = QFontMetrics(option.font)
            painter.drawText(QRectF(name_left, inner.top(), max(0.0, name_width), inner.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             metrics.elidedText(entry.name, Qt.TextElideMode.ElideRight,
                                                max(0, int(name_width))))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            position = event.position()
            if self._checkbox_rect(option.rect).adjusted(-2, -2, 2, 2).contains(position):
                self.view.setCurrentIndex(index)
                if (event.type() == QEvent.Type.MouseButtonRelease
                        and event.button() == Qt.MouseButton.LeftButton):
                    state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole))
                    model.setData(index,
                                  Qt.CheckState.Unchecked if state == Qt.CheckState.Checked
                                  else Qt.CheckState.Checked,
                                  Qt.ItemDataRole.CheckStateRole)
                return True
        return super().editorEvent(event, model, option, index)


class CustomFlowDialog(Dialog):
    def __init__(self, parent, task):
        super().__init__(parent, task.tr('自定义任务流'),
                         ok_text=task.tr('保存并关闭'), cancel_text=task.tr('取消'), width=1080)
        self.task = task
        self.store = FlowStepStore(task.executor.config_folder if task.executor else 'configs')
        self.flows = self.store.load()
        self.active_name = task.config.get(FLOW_SELECTOR, DEFAULT_FLOW_NAME)
        if self.active_name not in self.flows:
            self.active_name = DEFAULT_FLOW_NAME
        self._loading = True
        self._form = None

        root = QWidget(self.panel)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_step_column(root), 0)
        layout.addWidget(self._build_detail_column(root), 1)
        self.body.addWidget(root)
        self._load_steps()
        self._loading = False

    # ------------------------------------------------------------- left column
    def _build_step_column(self, parent):
        column = QWidget(parent)
        column.setFixedWidth(344)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = Card(look='inset')
        inner = QVBoxLayout(card)
        inner.setContentsMargins(14, 14, 14, 12)
        inner.setSpacing(10)

        flow_row = QHBoxLayout()
        flow_row.setSpacing(6)
        flow_row.addWidget(Label(self.task.tr('任务流'), 'secondary'), 0)
        self.flow_combo = ComboBox(list(self.flows))
        self.flow_combo.setCurrentText(self.active_name)
        self.flow_combo.currentTextChanged.connect(self._flow_changed)
        flow_row.addWidget(self.flow_combo, 1)
        self.new_button = Button(icon='plus', variant='subtle', parent=card)
        self.new_button.setToolTip(self.task.tr('新建任务流'))
        self.new_button.clicked.connect(self._new_flow)
        flow_row.addWidget(self.new_button)
        self.delete_button = Button(icon='trash', variant='subtle', parent=card)
        self.delete_button.setToolTip(self.task.tr('删除当前任务流'))
        self.delete_button.clicked.connect(self._delete_flow)
        flow_row.addWidget(self.delete_button)
        inner.addLayout(flow_row)

        list_row = QHBoxLayout()
        list_row.setSpacing(6)
        list_row.addWidget(Label(self.task.tr('执行顺序'), 'section'), 0)
        list_row.addStretch(1)
        self.all_on_button = Button(self.task.tr('全部启用'), variant='subtle', size='small', parent=card)
        self.all_on_button.clicked.connect(lambda: self._set_all_checked(True))
        self.all_off_button = Button(self.task.tr('全部停用'), variant='subtle', size='small', parent=card)
        self.all_off_button.clicked.connect(lambda: self._set_all_checked(False))
        list_row.addWidget(self.all_on_button)
        list_row.addWidget(self.all_off_button)
        inner.addLayout(list_row)

        self.step_list = QListWidget(card)
        self.step_list.setObjectName('customFlowStepList')
        self.step_list.setStyleSheet(
            'QListWidget#customFlowStepList { background: transparent; border: 0; }')
        self.step_list.setItemDelegate(_StepDelegate(self.step_list, self.step_list))
        self.step_list.setUniformItemSizes(True)
        self.step_list.setMouseTracking(True)
        self.step_list.setFrameShape(QFrame.Shape.NoFrame)
        self.step_list.setSpacing(0)
        self.step_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.step_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.step_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.step_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.step_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_list.setMinimumHeight(260)
        self.step_list.currentItemChanged.connect(self._item_changed)
        self.step_list.itemChanged.connect(self._item_checked)
        self.step_list.model().rowsMoved.connect(self._rows_moved)
        inner.addWidget(self.step_list, 1)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.add_combo = ComboBox([entry.name for entry in ENTRIES])
        self.add_combo.setToolTip(self.task.tr('选择要添加的步骤'))
        add_row.addWidget(self.add_combo, 1)
        self.add_button = Button(self.task.tr('添加'), icon='plus', variant='tonal', size='small', parent=card)
        self.add_button.clicked.connect(self._add_step)
        add_row.addWidget(self.add_button)
        inner.addLayout(add_row)

        order_row = QHBoxLayout()
        order_row.setSpacing(6)
        self.up_button = Button(self.task.tr('上移'), icon='chevron-up', variant='subtle', size='small', parent=card)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button = Button(self.task.tr('下移'), icon='chevron-down', variant='subtle', size='small', parent=card)
        self.down_button.clicked.connect(lambda: self._move(1))
        self.delete_step_button = Button(self.task.tr('删除步骤'), icon='trash', variant='subtle', size='small',
                                         parent=card)
        self.delete_step_button.clicked.connect(self._delete_step)
        order_row.addWidget(self.up_button)
        order_row.addWidget(self.down_button)
        order_row.addStretch(1)
        order_row.addWidget(self.delete_step_button)
        inner.addLayout(order_row)

        layout.addWidget(card, 1)
        return column

    # ------------------------------------------------------------ right column
    def _build_detail_column(self, parent):
        column = QWidget(parent)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card = Card(look='inset')
        inner = QVBoxLayout(card)
        inner.setContentsMargins(20, 18, 20, 16)
        inner.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.step_title = Label('', 'title', card)
        self.step_description = Label('', 'secondary', card, word_wrap=True)
        text.addWidget(self.step_title)
        text.addWidget(self.step_description)
        header.addLayout(text, 1)
        self.reset_button = Button(self.task.tr('恢复默认'), icon='reset', variant='subtle', size='small', parent=card)
        self.reset_button.setToolTip(self.task.tr('把该步骤的设置恢复成默认值'))
        self.reset_button.clicked.connect(self._reset_step)
        header.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignTop)
        inner.addLayout(header)

        self.tabs = TabStrip('underline')
        self.tabs.add_tab('settings', self.task.tr('设置'), icon='gear')
        self.tabs.add_tab('info', self.task.tr('说明'), icon='info')
        self.tabs.current_changed.connect(self._show_tab)
        inner.addWidget(self.tabs)

        self.pages = QWidget(card)
        pages_layout = QVBoxLayout(self.pages)
        pages_layout.setContentsMargins(0, 0, 0, 0)
        pages_layout.setSpacing(0)

        self.form_scroll = QScrollArea(self.pages)
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pages_layout.addWidget(self.form_scroll)

        self.info_scroll = QScrollArea(self.pages)
        self.info_scroll.setWidgetResizable(True)
        self.info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.info_page = QWidget(self.info_scroll)
        self.info_layout = QVBoxLayout(self.info_page)
        self.info_layout.setContentsMargins(0, 0, 0, 0)
        self.info_layout.setSpacing(10)
        self.info_scroll.setWidget(self.info_page)
        self.info_scroll.hide()
        pages_layout.addWidget(self.info_scroll)
        inner.addWidget(self.pages, 1)

        self.hint = Label(self.task.tr('勾选纳入执行；拖拽或上移/下移调整顺序，从上到下依次执行。'),
                          'caption', card, word_wrap=True)
        inner.addWidget(self.hint)

        layout.addWidget(card, 1)
        return column

    # --------------------------------------------------------------- helpers
    def _current_items(self):
        return self.flows.get(self.active_name, default_items())

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
            widget_item.setToolTip(entry.description or entry.name)
            self.step_list.addItem(widget_item)
        self.step_list.blockSignals(False)
        self.step_list.setCurrentRow(0 if self.step_list.count() else -1)
        self._render_detail(self.step_list.item(0) if self.step_list.count() else None)

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

    def _clear_layout(self, layout):
        while layout.count():
            old = layout.takeAt(0)
            widget = old.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render_detail(self, widget_item):
        if self._form is not None:
            self.form_scroll.takeWidget()
            self._form.setParent(None)
            self._form.deleteLater()
            self._form = None
        self._clear_layout(self.info_layout)
        if widget_item is None:
            self.step_title.setText(self.task.tr('未选择步骤'))
            self.step_description.setText(self.task.tr('在左侧选择一个步骤来编辑它的设置。'))
            self.reset_button.setEnabled(False)
            self.tabs.setEnabled(False)
            self.pages.setEnabled(False)
            self.form_scroll.setWidget(QWidget(self.form_scroll))
            return
        self.tabs.setEnabled(True)
        self.pages.setEnabled(True)
        step_id = widget_item.data(ID_ROLE)
        entry = ENTRIES_BY_ID[step_id]
        self.step_title.setText(entry.name)
        self.step_description.setText(entry.description or '')
        self.reset_button.setEnabled(bool(entry.default_config))

        params = deepcopy(widget_item.data(PARAMS_ROLE) or entry.default_config)
        if entry.default_config:
            def on_change(new_values):
                widget_item.setData(PARAMS_ROLE, deepcopy(new_values))

            config = FlowItemConfig(params, entry.default_config, on_change)
            config_type = self.task.config_type_for(step_id)
            stub = _FlowItemStub(entry, config_type)
            self._form = ConfigForm(stub, config, entry.default_config,
                                    entry.config_description, config_type, self.form_scroll)
            self.form_scroll.setWidget(self._form)
        else:
            self._form = QWidget(self.form_scroll)
            empty_layout = QVBoxLayout(self._form)
            empty_layout.setContentsMargins(0, 8, 0, 0)
            empty_layout.setSpacing(6)
            empty_layout.addWidget(Label(self.task.tr('此步骤无需设置，勾选后即会执行。'), 'secondary',
                                         self._form, word_wrap=True))
            empty_layout.addStretch(1)
            self.form_scroll.setWidget(self._form)

        self._fill_info(entry, params)
        self._show_tab(self.tabs.current_key() or 'settings')

    def _fill_info(self, entry, params):
        if entry.description:
            self.info_layout.addWidget(Label(entry.description, 'secondary', self.info_page, word_wrap=True))
        group = Card(look='inset')
        rows = QVBoxLayout(group)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        settings = []
        for key, default in entry.default_config.items():
            current = params.get(key, default)
            description = entry.config_description.get(key, '')
            hint = description.split('。')[0].split('；')[0] if description else ''
            if isinstance(current, bool):
                shown = '已开启' if current else '未开启'
            elif current in ('', None):
                shown = self.task.tr('未设置')
            else:
                shown = str(current)
            settings.append((key, hint, shown))
        if not settings:
            settings.append((self.task.tr('无需设置'), self.task.tr('该步骤没有可配置的参数。'), ''))
        for index, (title, description, value) in enumerate(settings):
            control = None
            if value:
                control = Label(value, 'secondary')
                control.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row = SettingRow(title, description, control, control_width=220, indent=False)
            row.set_first(index == 0)
            rows.addWidget(row)
        self.info_layout.addWidget(group)
        self.info_layout.addStretch(1)

    def _show_tab(self, key):
        info = key == 'info'
        self.info_scroll.setVisible(info)
        self.form_scroll.setVisible(not info)

    def _set_all_checked(self, checked):
        self.step_list.blockSignals(True)
        for index in range(self.step_list.count()):
            item = self.step_list.item(index)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.step_list.blockSignals(False)
        if self.step_list.count():
            self.step_list.viewport().update()
        self.flows[self.active_name] = self._collect_steps()

    def _reset_step(self):
        widget_item = self.step_list.currentItem()
        if widget_item is None:
            return
        entry = ENTRIES_BY_ID[widget_item.data(ID_ROLE)]
        widget_item.setData(PARAMS_ROLE, deepcopy(entry.default_config))
        self._render_detail(widget_item)
        self.flows[self.active_name] = self._collect_steps()
        show_toast(self, self.task.tr('已恢复该步骤的默认设置'), tone='success')

    # --------------------------------------------------------------- slots
    def _item_changed(self, current, previous):
        if self._loading:
            return
        if previous is not None:
            previous.setData(PARAMS_ROLE, previous.data(PARAMS_ROLE))
        self.flows[self.active_name] = self._collect_steps()
        self._render_detail(current)
        self._sync_buttons()

    def _item_checked(self, widget_item):
        if self._loading:
            return
        self.flows[self.active_name] = self._collect_steps()
        self._update_summary(widget_item)
        self.step_list.viewport().update()

    def _update_summary(self, widget_item):
        entry = ENTRIES_BY_ID[widget_item.data(ID_ROLE)]
        pairs = describe_params(entry, widget_item.data(PARAMS_ROLE) or {})
        lines = [entry.description or '']
        if pairs:
            lines.append(' · '.join(pairs))
        widget_item.setToolTip('\n'.join(line for line in lines if line))

    def _rows_moved(self, *_):
        if self._loading:
            return
        self.flows[self.active_name] = self._collect_steps()
        self._sync_buttons()

    def _sync_buttons(self):
        row = self.step_list.currentRow()
        count = self.step_list.count()
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count - 1)
        self.delete_step_button.setEnabled(count > 0)

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
        self.flows[self.active_name] = self._collect_steps()
        self._sync_buttons()

    def _add_step(self):
        name = self.add_combo.currentText()
        task_id = next((entry.task_id for entry in ENTRIES if entry.name == name), None)
        if task_id is None:
            return
        item = new_item(task_id)
        entry = ENTRIES_BY_ID[task_id]
        widget_item = QListWidgetItem(entry.name)
        widget_item.setFlags(widget_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        widget_item.setCheckState(Qt.CheckState.Checked)
        widget_item.setData(ID_ROLE, task_id)
        widget_item.setData(UID_ROLE, item['uid'])
        widget_item.setData(PARAMS_ROLE, deepcopy(item['params']))
        widget_item.setToolTip(entry.description or entry.name)
        # 插到选中条目之后, 方便按执行顺序连续编排; 没有选中时仍追加到末尾。
        current_row = self.step_list.currentRow()
        if current_row >= 0:
            self.step_list.insertItem(current_row + 1, widget_item)
        else:
            self.step_list.addItem(widget_item)
        self.step_list.setCurrentItem(widget_item)
        self.flows[self.active_name] = self._collect_steps()
        self._sync_buttons()

    def _delete_step(self):
        row = self.step_list.currentRow()
        if row < 0:
            return
        self._loading = True
        self.step_list.takeItem(row)
        self._loading = False
        self.flows[self.active_name] = self._collect_steps()
        if self.step_list.count():
            self.step_list.setCurrentRow(min(row, self.step_list.count() - 1))
        else:
            self._render_detail(None)
        self._sync_buttons()

    def _flow_changed(self, name):
        if self._loading or not name:
            return
        self._loading = True
        self.flows[self.active_name] = self._collect_steps()
        self.active_name = name
        self._load_steps()
        self._loading = False
        self._sync_buttons()

    def _new_flow(self):
        # 静态 QInputDialog.getText() 创建的是原生弹窗, 不继承主窗口的深色
        # stylesheet, 在深色主题下显示为刺眼的浅色块。用实例 + theme.install
        # 让它跟随主题(weakref 注册, 关闭后自动清理)。
        dialog = QInputDialog(self)
        dialog.setWindowTitle(self.task.tr('新建任务流'))
        dialog.setLabelText(self.task.tr('任务流名称'))
        dialog.setTextValue('')
        theme.install(dialog)
        ok = dialog.exec() == QInputDialog.Accepted
        name = dialog.textValue() if ok else ''
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.flows:
            show_toast(self, self.task.tr('任务流已存在'), tone='warning')
            return
        self.flows[self.active_name] = self._collect_steps()
        self.flows[name] = default_items()
        self.flow_combo.blockSignals(True)
        self.flow_combo.addItem(name)
        self.flow_combo.setCurrentText(name)
        self.flow_combo.blockSignals(False)
        self._loading = True
        self.active_name = name
        self._load_steps()
        self._loading = False
        self._sync_buttons()
        show_toast(self, self.task.tr('已新建任务流：') + name, tone='success')

    def _delete_flow(self):
        if len(self.flows) <= 1:
            show_toast(self, self.task.tr('至少保留一个任务流'), tone='warning')
            return
        name = self.active_name
        self.flows.pop(name, None)
        self.flow_combo.blockSignals(True)
        self.flow_combo.removeItem(self.flow_combo.findText(name))
        self.flow_combo.blockSignals(False)
        self._loading = True
        self.active_name = self.flow_combo.currentText()
        self._load_steps()
        self._loading = False
        self._sync_buttons()
        show_toast(self, self.task.tr('已删除任务流：') + name, tone='success')

    # -------------------------------------------------------------- dialog
    def _fit(self):
        window = self.parentWidget()
        if window is None:
            super()._fit()
            return
        self.panel.setFixedWidth(min(1140, max(760, window.width() - 96)))
        self.panel.setMinimumHeight(min(780, max(520, window.height() - 96)))
        super()._fit()

    def open(self):
        self._sync_buttons()
        super().open()

    def validate(self):
        self.flows[self.active_name] = self._collect_steps()
        self.store.save(self.flows)
        self.task.config[FLOW_SELECTOR] = self.active_name
        config_type = self.task.config_type.get(FLOW_SELECTOR)
        if isinstance(config_type, dict) and 'options' in config_type:
            config_type['options'] = list(self.flows)
        enabled = sum(1 for row in self.flows[self.active_name] if row['enabled'])
        show_toast(self, self.task.tr('任务流已保存：') + f'{self.active_name} · {enabled} 个启用步骤',
                   tone='success')
        return True
