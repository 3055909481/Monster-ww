"""Icons for the daily routine editor.

Two layers:

* Step icons reuse the project's existing task artwork under ``icons/tasks``.
* Dropdown option icons are generated as small colour-coded badges so every
  region / weapon / boss / dungeon / material is visually distinct without
  shipping new image assets.

If real portraits become available, drop them into ``icons/routine`` and add
an entry to ``OPTION_ICON_FILES`` (key = "<config key>|<option text>"); the
file is preferred over the generated badge.
"""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from Mon.ui.qt.tasks.config_editors import DropDownEditor
from Mon.ui.qt.tasks.config_form import ConfigForm

ROOT = Path(__file__).resolve().parents[2]
TASK_ICON_DIR = ROOT / 'icons' / 'tasks'
OPTION_ICON_DIR = ROOT / 'icons' / 'routine'

# task id -> file under icons/tasks
STEP_ICON_FILES = {
    'claim_daily': 'daily.png',
    'battle_pass': 'category-daily.png',
    'weekly_garden': 'garden.png',
    'simulation': 'simulation.png',
    'forgery': 'forgery.png',
    'tacet': 'tacet.png',
    'boss_challenge': 'boss.png',
    'weekly_boss': 'boss.png',
}

# Optional real portraits: "<config key>|<option text>" -> file under icons/routine
OPTION_ICON_FILES = {
    # 'BOSS|无冠者': 'boss-wuguanzhe.png',
    # '周本|赫卡忒': 'weekly-hekate.png',
}

# Config key -> badge colour. Keeps categories instantly recognisable.
CATEGORY_COLORS = {
    '地区': '#3D7EBF',        # blue - region
    '材料': '#C07A19',        # amber - material
    '武器类型': '#2E8B57',    # green - weapon type
    '无音区': '#7A4FAB',      # purple - tacet zone
    'BOSS': '#B23A3A',        # red - boss challenge
    '周本': '#C05621',        # deep orange - weekly boss
}

# Short label (1-2 chars) shown on a badge, keyed by option text.
OPTION_LABELS = {
    'Shell Credit': '贝',
    'Resonator EXP': '共',
    'Weapon EXP': '武',
}

BADGE_SIZE = 26


def task_icon(task_id):
    """Return the QIcon for a pipeline step, or an empty QIcon if unmapped."""
    filename = STEP_ICON_FILES.get(task_id)
    if filename:
        path = TASK_ICON_DIR / filename
        if path.exists():
            return QIcon(str(path))
    return QIcon()


def _badge_label(key, text):
    text = str(text)
    if text in OPTION_LABELS:
        return OPTION_LABELS[text]
    if key == '地区' and '·' in text:
        # 瑾珑·梦州 -> 梦州 ; 索拉里斯之极·拉海洛 -> 拉海洛
        tail = text.split('·')[-1]
        return tail[:2]
    if key in ('BOSS', '周本') and text.endswith('无音区'):
        return text[:2]
    return text[:2]


def option_icon(key, text, size=BADGE_SIZE):
    """Build a colour-coded badge QIcon for one dropdown option."""
    text = str(text)
    real = OPTION_ICON_FILES.get(f'{key}|{text}')
    if real:
        path = OPTION_ICON_DIR / real
        if path.exists():
            return QIcon(str(path))

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(CATEGORY_COLORS.get(key, '#5A6472')))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)

    label = _badge_label(key, text)
    font = QFont()
    font.setPixelSize(max(10, int(size * (0.58 if len(label) > 1 else 0.72))))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor('#FFFFFF'))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, label)
    painter.end()
    return QIcon(pixmap)


class IconConfigForm(ConfigForm):
    """ConfigForm that decorates known dropdowns with option icons.

    Overrides the framework's editor factory instead of patching framework
    source: the returned DropDownEditor already holds a ComboBox, so we only
    set item icons on it after construction.
    """

    def _create_config_widget(self, key, value):
        widget = super()._create_config_widget(key, value)
        if isinstance(widget, DropDownEditor) and key in CATEGORY_COLORS:
            combo = widget.combo_box
            for index, option in enumerate(widget.options):
                if index < combo.count():
                    combo.setItemIcon(index, option_icon(key, option))
            combo.setIconSize(QSize(BADGE_SIZE, BADGE_SIZE))
        return widget
