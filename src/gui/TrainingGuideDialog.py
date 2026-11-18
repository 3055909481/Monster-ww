"""Information dialog for the semi-automatic training-guide task.

All real configuration (stamina budget + per-category toggles) lives on the
task card itself and is rendered by the framework from default_config. This
dialog just explains the mapping and behaviour so users know what the toggles
mean, since the material panel itself only shows icons and group text.
"""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from Mon.ui.qt.kit.dialogs import Dialog


class TrainingGuideDialog(Dialog):
    def __init__(self, parent, task):
        super().__init__(parent, task.tr('Training guide (automatic)'),
                         ok_text=task.tr('Close'), cancel_text=None, width=580)
        root = QWidget(self.panel)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        intro = QLabel(task.tr(
            'Configure this task card: set 总体力预算 and toggle the categories to fill. '
            'The task opens 素材获取 → 培养目标, reads each row, and only farms rows '
            'that offer 直接挑战 with a real shortage (拥有 &lt; 需要); 前往 rows are skipped.'
        ), root)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        mapping = QLabel(task.tr(
            'Group → category mapping:\n'
            '  共鸣者突破材料 → 讨伐强敌（补讨伐强敌）\n'
            '  武器与技能素材 → 凝素领域（补凝素领域）\n'
            '  技能升级材料 / 突破材料 → 战歌重奏（补战歌重奏）\n\n'
            'Each fight costs the domain\'s stamina (凝素 40 / 讨伐 60 / 战歌 60). '
            'The task stops when the total budget is spent or enabled rows are satisfied.'
        ), root)
        mapping.setWordWrap(True)
        layout.addWidget(mapping)

        note = QLabel(task.tr(
            'Note: the game decides which specific boss/domain a 直接挑战 button opens. '
            'If detection fails on your resolution, adjust the constants in '
            'src/task/TrainingGuideTask.py (Config class) and rebuild.'
        ), root)
        note.setWordWrap(True)
        layout.addWidget(note)

        self.body.addWidget(root)

    def validate(self):
        return True
