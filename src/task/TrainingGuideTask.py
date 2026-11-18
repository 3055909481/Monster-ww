"""培养目标自动补齐任务。

流程：

    回到主界面 → 打开素材获取 → 培养目标 → 选择目标角色 → 所需材料页
    → OCR 每行的分组文字 + 拥有/需要数字 + 「直接挑战」按钮
    → 按配置开关筛选启用的副本类别
    → 点「直接挑战」→ 游戏自动进副本 → 战斗 → 退出
    → 重开面板，在总体力预算内循环，直到预算耗尽或缺口补齐

设计要点：

* 材料格只有图标没有中文名，但「分组文字」可读，因此按分组映射到副本类别。
* 显示「前往」的行跳过——游戏没有为它提供直接挑战。
* 具体进哪个副本由游戏决定，脚本不需要选副本/定位。
* 所有屏幕相关参数集中在 Config，便于真机标定。
"""

import math
import re

from Mon import Logger

from src.task.DomainTask import DomainTask
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)

NUMBER_RE = re.compile(r'(\d+)\s*/\s*(\d+)')

# 配置键
BUDGET_KEY = '总体力预算'
ROLE_KEY = '培养目标角色'
CATEGORY_KEYS = {
    'forgery': '补凝素领域',
    'boss_challenge': '补讨伐强敌',
    'weekly_boss': '补战歌重奏',
}

# 分组文字 -> 副本类别
GROUP_TO_DOMAIN = {
    '共鸣者突破材料': 'boss_challenge',
    '武器与技能素材': 'forgery',
    '技能升级材料': 'weekly_boss',
    '突破材料': 'weekly_boss',
}

# 每类副本的单次体力消耗 / 期望产出
DOMAIN_STAMINA = {'forgery': 40, 'boss_challenge': 60, 'weekly_boss': 60}
DOMAIN_YIELD = {'forgery': 2, 'boss_challenge': 1, 'weekly_boss': 1}


class Config:
    """真机可调的屏幕/流程参数。"""

    # 打开「素材获取」面板的多策略，按顺序尝试
    OPEN_PANEL_STEPS = [
        ('ocr', '素材获取'),
        ('ocr', '培养目标'),
        ('key', 'Guidebook Key'),
    ]

    WAIT_PANEL_TIMEOUT = 6
    WAIT_COMBAT_TIMEOUT = 15
    COMBAT_TIMEOUT = 180

    MAX_ROWS = 20

    # OCR 识别区域（相对屏幕坐标）
    ROWS_REGION = (0.36, 0.12, 0.99, 0.92)
    # 选择角色界面里角色所在区域
    ROLE_LIST_REGION = (0.02, 0.28, 0.63, 0.92)

    SAME_ROW_TOLERANCE = 0.05

    CHALLENGE_TEXTS = ('直接挑战', '挑战')
    CONFIRM_TEXTS = ('确定设置', '确定', '保存')

    AFTER_CLICK_SLEEP = 2
    AFTER_EXIT_SLEEP = 2


class TrainingGuideTask(DomainTask):
    """在体力预算内，按启用类别自动补齐培养目标材料缺口。

    继承 DomainTask 以复用 combat_once 和 make_sure_in_world。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '培养目标自动补齐'
        self.icon = task_icon('daily')
        self.group_name = '日常活动'
        self.group_icon = task_icon('category-daily')
        self.description = '按体力预算和类别开关，自动补齐培养目标缺口（需先在界面选好角色）。'
        self.support_schedule_task = True

        # 目标角色：留空表示沿用游戏里当前已设置的培养目标
        self.default_config[ROLE_KEY] = ''
        self.config_description[ROLE_KEY] = (
            '要补齐的目标角色中文名（如 西格莉卡）。留空则使用游戏里当前已设置的培养目标。'
        )
        self.config_type[ROLE_KEY] = {'type': 'line_edit'}

        self.default_config[BUDGET_KEY] = 240
        self.config_description[BUDGET_KEY] = (
            '本次运行的总体力预算；0 表示不限。凝素每次 40、讨伐/战歌每次 60。'
        )
        self.config_type[BUDGET_KEY] = {'min': 0, 'max': 100000}

        for domain, key in CATEGORY_KEYS.items():
            # bool 默认值 -> 框架自动渲染开关，无需显式 type
            self.default_config[key] = domain != 'weekly_boss'
            label = {'forgery': '凝素领域', 'boss_challenge': '讨伐强敌',
                     'weekly_boss': '战歌重奏'}[domain]
            self.config_description[key] = f'是否补齐{label}类别的缺口。'

        self.add_exit_after_config()
        self.processed = 0
        self.stamina_spent = 0

    # ---------------------------------------------------------------- 入口
    def run(self):
        DomainTask.run(self)
        self.ensure_main(time_out=180)
        self.log_info(self.tr('培养目标自动补齐：开始'))

        budget = int(self.config.get(BUDGET_KEY, 0) or 0)
        role = str(self.config.get(ROLE_KEY, '') or '').strip()
        enabled = self._enabled_domains()
        self.log_info(self.tr('启用类别：{items}；体力预算：{budget}；目标角色：{role}').format(
            items=list(enabled), budget=budget, role=role or '（沿用当前）'))
        if not enabled:
            self.log_info(self.tr('未启用任何类别，结束'))
            return

        processed = 0
        spent = 0
        no_progress = 0

        # 首轮：选角色（如配置）；之后每轮只打开面板读缺口
        first_round = True
        while True:
            if budget and spent >= budget:
                self.log_info(self.tr('体力预算已用完（{spent}/{budget}）').format(
                    spent=spent, budget=budget))
                break

            if not self._open_material_panel():
                self.log_error(self.tr('无法打开素材获取面板，结束'))
                break

            if first_round and role:
                if not self._select_role(role):
                    self.log_warning(self.tr('未能选择目标角色 {role}，沿用当前培养目标').format(role=role))
                first_round = False

            rows = self._read_rows()
            self.log_info(self.tr('识别到 {count} 行可挑战缺口').format(count=len(rows)))
            if not rows:
                break

            handled = 0
            for row in rows:
                domain = row['domain']
                if domain not in enabled or domain not in DOMAIN_STAMINA:
                    continue
                have, need = row['have'], row['need']
                if have >= need:
                    continue
                cost = DOMAIN_STAMINA[domain]
                if budget and spent + cost > budget:
                    continue
                label = {'forgery': '凝素领域', 'boss_challenge': '讨伐强敌',
                         'weekly_boss': '战歌重奏'}.get(domain, domain)
                self.log_info(self.tr('{label}：{have}/{need}，体力 {cost}').format(
                    label=label, have=have, need=need, cost=cost))

                if self._farm_one(row):
                    handled += 1
                    processed += 1
                    spent += cost
                    if budget and spent >= budget:
                        break

            if handled == 0:
                no_progress += 1
                if no_progress >= 2:
                    self.log_info(self.tr('连续两轮无进展，结束'))
                    break
            else:
                no_progress = 0

        self.processed = processed
        self.stamina_spent = spent
        self.log_info(self.tr('培养目标补齐完成：挑战 {n} 次，消耗体力 {s}').format(
            n=processed, s=spent))
        self.info_set('培养目标_挑战次数', processed)
        self.info_set('培养目标_消耗体力', spent)

    def _enabled_domains(self):
        return {domain for domain, key in CATEGORY_KEYS.items()
                if self.config.get(key, False)}

    # ------------------------------------------------------------ 面板/角色
    def _open_material_panel(self):
        for step in Config.OPEN_PANEL_STEPS:
            kind = step[0]
            try:
                if kind == 'ocr':
                    boxes = self.ocr(match=re.compile(step[1]))
                    if boxes:
                        self.click_box(boxes[0], after_sleep=Config.WAIT_PANEL_TIMEOUT)
                        if self._on_material_page():
                            return True
                elif kind == 'key':
                    key = self.key_config.get(step[1])
                    if key:
                        self.send_key(key, after_sleep=Config.WAIT_PANEL_TIMEOUT)
                        if self._on_material_page():
                            return True
                elif kind == 'click':
                    self.click_relative(step[1], step[2], after_sleep=Config.WAIT_PANEL_TIMEOUT)
                    if self._on_material_page():
                        return True
            except Exception as exc:  # noqa: BLE001
                self.log_warning(self.tr('打开步骤 {step} 失败：{error}').format(step=step, error=exc))

        self.screenshot('培养目标_面板打开失败')
        return False

    def _on_material_page(self):
        for text in ('所需材料', '培养目标') + Config.CHALLENGE_TEXTS:
            if self.ocr(match=re.compile(text)):
                return True
        return False

    def _select_role(self, role):
        """在「选择培养目标」界面点角色名并确认；已是当前角色则跳过。"""
        # 当前角色已匹配则无需切换
        try:
            current = self.ocr(match=re.compile(role))
            if current:
                # 出现在右侧角色展示区说明已是当前目标
                self.log_info(self.tr('培养目标已是 {role}，跳过选择').format(role=role))
                return True
        except Exception:  # noqa: BLE001
            pass

        x1, y1, x2, y2 = Config.ROLE_LIST_REGION
        try:
            boxes = self.ocr(x1, y1, x2, y2, match=re.compile(re.escape(role)))
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('识别角色列表失败：{error}').format(error=exc))
            boxes = []
        if not boxes:
            self.screenshot('培养目标_角色未找到')
            return False

        self.click_box(boxes[0], after_sleep=1)
        # 点确定设置（若出现）
        for text in Config.CONFIRM_TEXTS:
            try:
                confirm = self.ocr(match=re.compile(text))
                if confirm:
                    self.click_box(confirm[0], after_sleep=Config.WAIT_PANEL_TIMEOUT)
                    break
            except Exception:  # noqa: BLE001
                continue
        return self._on_material_page()

    # ---------------------------------------------------------------- 识别
    def _read_rows(self):
        """OCR 行 -> {domain, have, need, button}，仅保留有「直接挑战」的行。"""
        x1, y1, x2, y2 = Config.ROWS_REGION
        try:
            boxes = self.ocr(x1, y1, x2, y2)
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('识别材料行失败：{error}').format(error=exc))
            self.screenshot('培养目标_行识别失败')
            return []

        groups, numbers, challenges = [], [], []
        for box in boxes:
            text = (box.name or '').strip()
            domain = self._match_group(text)
            if domain is not None:
                groups.append((box, domain))
            if any(token in text for token in Config.CHALLENGE_TEXTS):
                challenges.append(box)
            match = NUMBER_RE.search(text)
            if match:
                numbers.append((box, int(match.group(1)), int(match.group(2))))

        rows = []
        for cbox in challenges:
            cy = cbox.center()[1]
            domain = self._nearest_domain(groups, cy)
            pair = self._nearest_numbers(numbers, cy)
            if domain is None or pair is None:
                continue
            rows.append({'button': cbox, 'domain': domain,
                         'have': pair[0], 'need': pair[1]})
        rows.sort(key=lambda r: r['button'].center()[1])
        return rows

    @staticmethod
    def _match_group(text):
        for group, domain in GROUP_TO_DOMAIN.items():
            if group in text:
                return domain
        return None

    def _nearest_domain(self, groups, y):
        best = None
        for box, domain in groups:
            d = abs(box.center()[1] - y)
            if d <= Config.SAME_ROW_TOLERANCE and (best is None or d < best[0]):
                best = (d, domain)
        return best[1] if best else None

    def _nearest_numbers(self, numbers, y):
        best = None
        for box, have, need in numbers:
            d = abs(box.center()[1] - y)
            if d <= Config.SAME_ROW_TOLERANCE and (best is None or d < best[0]):
                best = (d, have, need)
        return (best[1], best[2]) if best else None

    # ---------------------------------------------------------------- 战斗
    def _farm_one(self, row):
        """点「直接挑战」→ 战斗一次 → 退出."""
        try:
            self.click_box(row['button'], after_sleep=Config.AFTER_CLICK_SLEEP)
            self.wait_in_team_and_world(time_out=Config.WAIT_COMBAT_TIMEOUT)
            self.combat_once(wait_combat_time=Config.COMBAT_TIMEOUT, raise_if_not_found=False)
            self.make_sure_in_world()
            self.sleep(Config.AFTER_EXIT_SLEEP)
            return self.in_team_and_world()
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('挑战失败：{error}').format(error=exc))
            self.screenshot('培养目标_挑战失败')
            return False
