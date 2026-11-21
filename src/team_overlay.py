"""项目层补丁：在主界面顶部 RunBar 显示「识别到的当前队伍」。

框架 Mon-script 是 pip 包，不随本项目打包，因此不改框架源码。这里在
main.py 启动早期 monkeypatch ``RunBar.__init__``：原始构造完成后，在连接
状态胶囊右侧插入一条紧凑的队伍条（3 个圆头像 + 中文名），随刷新定时器更新。

识别数据与 coco-ww 一致：来自 executor 任务实例上的 ``task.chars``（战斗中
由 CharFactory 模板匹配认出的 3 个角色），不依赖 OCR、不额外截图，零性能开销。
未识别到队伍时保留队伍条：头像显示空圈、名字显示「未识别」。

头像资源在 assets/char_heads/<类名>.png，缺失时降级为名字首字。所有读取
均有防御性 try/except，任何一步失败都静默降级（顶部栏保持原样）。
"""

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QWidget

logger = logging.getLogger(__name__)

CHAR_HEADS = Path('assets') / 'char_heads'
_INSTALLED = False
_REFRESH_MS = 1000


def _class_name(char):
    """从角色实例取中文显示名（走框架 i18n，如 Verina -> 维里奈）。"""
    name = getattr(char, 'char_name', None)
    cls_name = ''
    try:
        from src.char.CharFactory import char_dict
        info = char_dict.get(name) or {}
        cls = info.get('cls')
        cls_name = getattr(cls, '__name__', '') or ''
    except Exception:
        pass
    if not cls_name:
        # 兜底：char_dict 查不到时直接用实例类名（自定义角色/加载失败场景）
        cls_name = type(char).__name__
    try:
        from Mon import og
        label = og.app.tr(cls_name) if og.app is not None and cls_name else (cls_name or name)
    except Exception:
        label = name
    return cls_name, label or name


def team_from_tasks(executor):
    """从 executor 任务里最近认出的完整 3 人队伍，按 1/2/3 号位。"""
    pools = []
    for pool_name in ('trigger_tasks', 'onetime_tasks'):
        pools.extend(getattr(executor, pool_name, None) or [])
    for task in pools:
        chars = getattr(task, 'chars', None)
        if not chars or len(chars) != 3 or any(char is None for char in chars):
            continue
        entries = [_class_name(char) for char in chars]
        if not all(cls for cls, _ in entries):
            continue
        if len({cls for cls, _ in entries}) != 3:
            continue
        return entries
    return None


def _tr(text):
    try:
        from Mon import og
        return og.app.tr(text) if og.app is not None and text else (text or '')
    except Exception:
        return text or ''


def _cover_rect(src_w, src_h, dst_w, dst_h):
    scale = max(dst_w / src_w, dst_h / src_h)
    w, h = src_w * scale, src_h * scale
    return ((dst_w - w) / 2, (dst_h - h) / 2, w, h)


class Avatar(QWidget):
    """圆形角色头像：有 PNG 就贴图，没有就画名字首字。"""

    def __init__(self, size=30, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = None
        self._text = ''
        self._key = None

    def set_char(self, cls_name, label):
        if cls_name == self._key:
            return
        self._key = cls_name
        path = CHAR_HEADS / f'{cls_name}.png'
        pixmap = QPixmap(str(path)) if cls_name and path.exists() else QPixmap()
        self._pixmap = None if pixmap.isNull() else pixmap
        self._text = (label or cls_name or '?')[:1]
        self.setToolTip(label or cls_name or '')
        self.update()

    def clear(self):
        """清空为未识别态：空圈、不贴图、不画字。"""
        if self._key is None and self._pixmap is None:
            return
        self._key = None
        self._pixmap = None
        self._text = ''
        self.setToolTip('')
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)
        painter.fillPath(path, QColor(255, 255, 255, 40))
        if self._pixmap is not None:
            x, y, w, h = _cover_rect(self._pixmap.width(), self._pixmap.height(),
                                     rect.width(), rect.height())
            painter.drawPixmap(rect, self._pixmap, QRectF(x, y, w, h))
        elif self._text:
            painter.setPen(QColor('#F4F4F5'))
            font = painter.font()
            font.setPixelSize(int(self.height() * 0.4))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        # _pixmap 与 _text 皆空时只画空圈（未识别态）
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)


class TeamStrip(QWidget):
    """顶部队伍条：3 个头像 + 中文名；未识别时显示空头像与「未识别」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._avatars = [Avatar(30, self) for _ in range(3)]
        for avatar in self._avatars:
            layout.addWidget(avatar)
        from Mon.ui.qt.kit.labels import Label
        self.name_label = Label('', 'secondary', self)
        layout.addWidget(self.name_label)
        self._last_signature = None
        # 刷新由 RunBar 已有的 1s timer 驱动，不另开定时器

    @property
    def executor(self):
        try:
            from Mon import og
            return getattr(og, 'executor', None)
        except Exception:
            return None

    def refresh(self):
        try:
            executor = self.executor
            team = team_from_tasks(executor) if executor is not None else None
            if not team:
                # 未识别：保留队伍条，头像清空为空圈，名字显示「未识别」
                if self._last_signature is not None:
                    for avatar in self._avatars:
                        avatar.clear()
                    self._last_signature = None
                if self.name_label.text() != _tr('未识别'):
                    self.name_label.setText(_tr('未识别'))
                    self.setToolTip('')
                self.show()
                return
            signature = tuple(cls for cls, _ in team)
            if signature != self._last_signature:
                self._last_signature = signature
                for avatar, (cls_name, label) in zip(self._avatars, team):
                    avatar.set_char(cls_name, label)
                self.name_label.setText(' · '.join(label for _, label in team))
                self.setToolTip(_tr('当前队伍') + ': ' + ' · '.join(label for _, label in team))
            self.show()
        except Exception as exc:
            logger.debug('team strip refresh skipped: %s', exc)


def _patched_run_bar_init(self, exit_event, parent=None):
    # 先执行原构造，再插入队伍条
    _original_run_bar_init(self, exit_event, parent)
    try:
        strip = TeamStrip(self)
        self._team_strip = strip
        layout = self.layout()
        # connection_pill 是第 0 个控件，插到它右侧、addStretch 之前
        layout.insertWidget(1, strip, 0, Qt.AlignmentFlag.AlignVCenter)
        strip.refresh()
        # 复用 RunBar 已有 1s timer 一起刷新，避免多开定时器
        self.timer.timeout.connect(strip.refresh)
    except Exception as exc:
        logger.warning('team overlay install skipped: %s', exc)


_original_run_bar_init = None


def install():
    """安装补丁，幂等。返回 True 表示本次实际安装。"""
    global _INSTALLED, _original_run_bar_init
    if _INSTALLED:
        return False
    try:
        from Mon.ui.qt.shell.run_bar import RunBar
        _original_run_bar_init = RunBar.__init__
        RunBar.__init__ = _patched_run_bar_init
        _INSTALLED = True
        return True
    except Exception as exc:
        logger.warning('team overlay patch skipped: %s', exc)
        return False
