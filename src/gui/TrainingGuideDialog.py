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
        super().__init__(parent, task.tr('培养目标自动补齐'),
                         ok_text=task.tr('关闭'), cancel_text=None, width=580)
        root = QWidget(self.panel)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        intro = QLabel(task.tr(
            '在任务卡片上设置「总体力预算」和要补齐的类别开关。'
            '任务会自动打开「素材获取 → 培养目标」，逐行读取缺口，'
            '只对显示「直接挑战」且确有缺口（拥有 &lt; 需要）的行进行补齐；'
            '显示「前往」的行会自动跳过。'
        ), root)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        mapping = QLabel(task.tr(
            '分组与副本类别对应关系：\n'
            '  共鸣者突破材料 → 讨伐强敌（补讨伐强敌）\n'
            '  武器与技能素材 → 凝素领域（补凝素领域）\n'
            '  技能升级材料 / 突破材料 → 战歌重奏（补战歌重奏）\n\n'
            '每次挑战消耗对应体力（凝素 40 / 讨伐 60 / 战歌 60）。'
            '体力预算用完，或已启用类别的缺口都补齐后，任务自动结束。'
        ), root)
        mapping.setWordWrap(True)
        layout.addWidget(mapping)

        note = QLabel(task.tr(
            '说明：点「直接挑战」后进入哪个具体副本由游戏决定，脚本无需选择。'
            '若在你的分辨率下识别失败，请调整 src/task/TrainingGuideTask.py '
            '中 Config 类的参数后重新打包。'
        ), root)
        note.setWordWrap(True)
        layout.addWidget(note)

        self.body.addWidget(root)

    def validate(self):
        return True
