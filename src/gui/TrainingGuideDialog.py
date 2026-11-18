"""Information dialog for the fully automatic training-guide task.

The task no longer asks users to pick domains or fill in shortages: it reads
the game's own 所需材料 panel and only acts on rows that offer a 直接挑战
button with a real shortage. This dialog explains that behaviour and surfaces
the tunable screen-detection settings, so nobody expects a manual table.
"""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from Mon.ui.qt.kit.dialogs import Dialog

from src.task.TrainingGuideTask import Config as GuideConfig


class TrainingGuideDialog(Dialog):
    def __init__(self, parent, task):
        super().__init__(parent, task.tr('Training guide (automatic)'),
                         ok_text=task.tr('Close'), cancel_text=None, width=560)
        root = QWidget(self.panel)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        intro = QLabel(task.tr(
            'The task opens 素材获取 → 培养目标 → 所需材料 by itself. It reads each '
            'row\'s "have/need" number and only farms rows that show a 直接挑战 '
            'button and still lack material. Rows showing 前往 are skipped because '
            'the game does not offer a direct challenge for them.'
        ), root)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        detail = QLabel(task.tr(
            'Because the game decides which domain a 直接挑战 button leads to, '
            'there is nothing to configure here. Set "每行刷取次数上限" on the task '
            'card to cap repeats per row (0 = until no shortage).'
        ), root)
        detail.setWordWrap(True)
        layout.addWidget(detail)

        tuning = QLabel(task.tr(
            'Detection region: {region}\n'
            'Same-row tolerance: {tol}\n'
            'Max rows per run: {cap}\n\n'
            'If detection fails on your resolution, adjust the constants in '
            'src/task/TrainingGuideTask.py (Config class) and rebuild.'
        ).format(region=GuideConfig.ROWS_REGION,
                 tol=GuideConfig.SAME_ROW_TOLERANCE,
                 cap=GuideConfig.MAX_ROWS), root)
        tuning.setWordWrap(True)
        layout.addWidget(tuning)

        self.body.addWidget(root)

    def validate(self):
        return True
