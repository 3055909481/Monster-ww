"""项目层补丁：在线更新前弹出确认框，避免静默更新。

框架 UpdateCard 的两个入口（关于页「更新到选中版本」按钮、新版本 toast 的
「更新」按钮）最终都会进入 UpdateCard.update_to_selected_version()，而该
方法直接开始下载/替换，没有任何确认。这里在 main.py 启动早期 monkeypatch
这一个方法：先展示目标版本与更新说明，用户点「确认更新」后才继续。

失败时静默降级为框架原行为，不影响原本的更新功能。
"""

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False
_ORIGINAL = None


def _tr(text):
    try:
        from Mon import og
        return og.app.tr(text) if og.app else text
    except Exception:
        return text


def _confirm(parent, title, message, ok_text):
    try:
        from Mon.ui.qt.kit.dialogs import confirm
        return bool(confirm(parent, title, message, ok_text=ok_text))
    except Exception as error:
        # 确认框不可用时不要阻断更新（降级为原行为）。
        logger.warning('update confirmation dialog unavailable: %s', error)
        return True


def _selected_version_info(card):
    """返回 (显示标题, 更新说明文本)，取不到时退回版本字符串。"""
    title = ''
    notes = ''
    try:
        index = card.version_combo.currentIndex()
        entry = card.versions[index] if 0 <= index < len(card.versions) else None
        if entry:
            title = str(entry.get('title') or entry.get('version') or '')
            raw = entry.get('update_note') or []
            if isinstance(raw, str):
                notes = raw
            elif isinstance(raw, (list, tuple)):
                notes = '\n'.join(str(line) for line in raw if str(line).strip())
    except Exception:
        pass
    return title, notes


def _install_progress(self, module):
    """注入进度回调：后台线程报数，QTimer 排到主线程改状态文字。

    框架 UpdateCard 只有「检查版本」转圈，下载/安装阶段没有可见反馈。
    这里复用它的 status_label 显示「下载更新文件 45/151 · 30%」，用户在
    整个更新过程中都能看到当前在做什么。任何一步失败都静默降级。
    """
    try:
        from PySide6.QtCore import QTimer

        def show(message):
            def apply():
                label = getattr(self, 'status_label', None)
                if label is not None:
                    label.setText(message)
                    label.setToolTip(message)
            QTimer.singleShot(0, apply)

        def progress(done, total, phase):
            if total and total > 0:
                percent = min(100, int(done * 100 / total))
                show(f'{phase} {done}/{total} · {percent}%')
            else:
                show(str(phase))

        setattr(module, 'progress_callback', progress)
    except Exception as error:
        logger.warning('failed to install update progress callback: %s', error)


def update_to_selected_version(self, checked=False):
    """包装原方法：先确认，再带进度显示真正开始更新。

    ``checked`` absorbs the bool Qt's ``clicked`` signal passes when this is
    wired directly to the Update button (the framework's own method relies on
    PyQt dropping that extra arg; keeping it explicit makes both call paths
    safe and keeps the function a plain Python descriptor after packaging).
    """
    global _ORIGINAL
    title, notes = _selected_version_info(self)
    message = _tr('即将下载并安装更新：{version}').format(version=title or _tr('选中版本'))
    if notes:
        message += '\n\n' + notes.strip()
    message += '\n\n' + _tr('更新过程中程序会退出并自动重启，请确认已保存当前工作。')
    if not _confirm(self, _tr('确认在线更新'), message, _tr('确认更新')):
        logger.info('online update cancelled by user')
        return
    if _ORIGINAL is None:
        logger.warning('original update_to_selected_version missing; aborting')
        return
    module = getattr(self, 'pyappify_module', None)
    if module is not None and hasattr(module, 'progress_callback'):
        _install_progress(self, module)
    return _ORIGINAL(self)


def install():
    """安装补丁；可重复调用，任何异常都降级为框架原行为。"""
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    try:
        from Mon.ui.qt.about.UpdateCard import UpdateCard
        _ORIGINAL = UpdateCard.update_to_selected_version
        UpdateCard.update_to_selected_version = update_to_selected_version
        _INSTALLED = True
        logger.info('online update confirmation installed')
    except Exception as error:
        logger.warning('failed to install online update confirmation: %s', error)
